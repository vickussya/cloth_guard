# Cloth Guard - Non-simulation clothing anti-clipping and shape-preservation tools for Blender
# Copyright (C) 2026 Vickussya
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Clipping detection (body vs garment), self-clipping detection and proximity weights."""

from __future__ import annotations

from dataclasses import dataclass

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ..constants import (
    BOUNDARY_CLIPPING_SCALE,
    BOUNDARY_CONTACT_SCALE,
    CG_VG_PINNED,
    CG_VG_PRESERVE_COLLAR,
    CG_VG_PRESERVE_HEM,
    CG_VG_PRESERVE_SEAMS,
    CG_VG_RISK,
    CORRECTION_PRESERVE_SCALE,
    DETECT_DEBUG_SAMPLES,
    DETECT_PRESERVE_CLIPPING_SCALE,
    DETECT_PRESERVE_CONTACT_SCALE,
    OVERLAP_BORDER_MIN_WEIGHT,
    OVERLAP_NEAR_SURFACE_FRACTION,
    PENETRATION_EPS_FRACTION,
    SELF_CLIP_PROBE_FRACTION,
)
from .body import BodySurface, build_body_surface, build_bvh
from .debug import log_debug
from .mesh_utils import (
    _boundary_vertex_mask,
    _build_vertex_adjacency,
    _neighbor_rings,
    _safe_avg,
    _safe_min,
    _vertex_group_index,
    _vertex_weight,
)


@dataclass(frozen=True)
class ProximityWeightsResult:
    weights: list[float]
    affected_count: int


@dataclass(frozen=True)
class ClipDetectionStats:
    checked_verts: int
    candidates_within_radius: int
    flagged_contact: int
    flagged_clipping: int
    min_nearest_distance: float | None
    avg_flagged_distance: float | None
    max_penetration: float | None  # deepest flagged vertex below the body surface, in meters


@dataclass(frozen=True)
class ClipDetectionResult:
    stats: ClipDetectionStats
    contact_weights: list[float]
    clipping_weights: list[float]
    nearest_distances: list[float | None]


def detect_clipping(
    *,
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    depsgraph,
    offset_distance: float,
    detection_radius: float,
    use_risk_area: bool,
    body_surface: BodySurface | None = None,
) -> ClipDetectionResult:
    """
    Robust MVP clipping detection on evaluated (deformed) meshes:
    - Proximity candidates via nearest surface distance (body-local BVH queries)
    - Contact weights for near-body vertices (CG_Contact)
    - Clipping weights for likely penetration (CG_Clipping), using surface normals (dot < 0)
    - Optional overlap heuristic, gated by a secondary penetration check (reduces false positives)

    body_surface: pass build_body_surface() once per frame when checking several garments.
    """
    offset_distance = max(0.0, float(offset_distance))
    detection_radius = max(offset_distance, float(detection_radius))
    if body_surface is None:
        body_surface = build_body_surface(body_obj, depsgraph)

    garment_eval = garment_obj.evaluated_get(depsgraph)
    garment_mesh = garment_eval.to_mesh()
    try:
        base_mesh = garment_obj.data
        base_vert_count = len(base_mesh.vertices)
        vertex_count_matches = len(garment_mesh.vertices) == base_vert_count
        if not vertex_count_matches:
            log_debug(
                bpy.context,
                "[Cloth Guard][Detect] WARNING: evaluated garment vertex count differs from base mesh. "
                "Topology-changing modifiers are likely enabled; CG_Clipping mapping to the base mesh may be incomplete."
            )

        body_mw = body_surface.matrix_world
        garment_mw = garment_eval.matrix_world
        garment_to_body = body_mw.inverted_safe() @ garment_mw

        # BVH is in evaluated BODY LOCAL SPACE.
        bvh_body = body_surface.bvh
        body_normal_sign = body_surface.normal_sign
        risk_idx = _vertex_group_index(garment_obj, CG_VG_RISK) if use_risk_area else None

        collar_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_COLLAR)
        hem_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_HEM)
        seams_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_SEAMS)
        preserve_scale_contact = DETECT_PRESERVE_CONTACT_SCALE
        preserve_scale_clipping = DETECT_PRESERVE_CLIPPING_SCALE

        # Boundary attenuation helps avoid noisy selections on open edges (collars/hem boundaries).
        boundary_mask = _boundary_vertex_mask(base_mesh)
        # Strongly protect open borders/silhouette edges from false positives.
        boundary_contact_scale = BOUNDARY_CONTACT_SCALE
        boundary_clipping_scale = BOUNDARY_CLIPPING_SCALE

        # When normals are noisy or inconsistent, require a small negative dot to call it penetration.
        penetration_eps = max(1e-6, offset_distance * PENETRATION_EPS_FRACTION)

        contact_weights: list[float] = [0.0] * base_vert_count
        clipping_weights: list[float] = [0.0] * base_vert_count
        nearest_distances: list[float | None] = [None] * base_vert_count
        # Positive = outside the body, negative = inside (None = no normal / not a candidate).
        signed_distances: list[float | None] = [None] * base_vert_count

        checked = 0
        candidates = 0
        debug_printed = 0

        for i, v in enumerate(garment_mesh.vertices):
            if i >= base_vert_count:
                break

            if risk_idx is not None:
                if _vertex_weight(base_mesh.vertices[i], risk_idx) <= 0.0:
                    continue

            checked += 1
            garment_in_body = garment_to_body @ v.co
            nearest = bvh_body.find_nearest(garment_in_body)
            if nearest is None:
                continue

            nearest_co, nearest_no, _, dist = nearest
            if nearest_no is not None:
                nearest_no = nearest_no * body_normal_sign  # make it point out of the body
            if dist is None:
                dist = (garment_in_body - nearest_co).length
            dist = float(dist)
            nearest_distances[i] = dist

            # A vertex deep inside the body is also "far" from the surface, so the unsigned
            # distance alone must not drop it from detection.
            deep_inside = (
                dist > detection_radius
                and nearest_no is not None
                and float((garment_in_body - nearest_co).dot(nearest_no)) < -penetration_eps
            )

            if dist <= detection_radius or deep_inside:
                candidates += 1
                if detection_radius == offset_distance:
                    contact_w = 1.0
                elif dist <= offset_distance or deep_inside:
                    contact_w = 1.0
                else:
                    contact_w = (detection_radius - dist) / (detection_radius - offset_distance)
                    contact_w = max(0.0, min(1.0, float(contact_w)))

                clip_w = 0.0
                if nearest_no is not None:
                    vec = garment_in_body - nearest_co
                    signed = float(vec.dot(nearest_no))
                    signed_distances[i] = signed
                    if signed < -penetration_eps:
                        # Depth-based weight (0..1 over ~offset_distance of penetration).
                        denom = max(1e-8, offset_distance)
                        clip_w = max(0.0, min(1.0, (-signed) / denom))

                preserve_w = 0.0
                if collar_idx is not None:
                    preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], collar_idx))
                if hem_idx is not None:
                    preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], hem_idx))
                if seams_idx is not None:
                    preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], seams_idx))
                if preserve_w > 0.0:
                    contact_w *= max(0.0, 1.0 - preserve_w * preserve_scale_contact)
                    clip_w *= max(0.0, 1.0 - preserve_w * preserve_scale_clipping)

                if boundary_mask[i]:
                    contact_w *= boundary_contact_scale
                    clip_w *= boundary_clipping_scale

                if contact_w > contact_weights[i]:
                    contact_weights[i] = contact_w
                if clip_w > clipping_weights[i]:
                    clipping_weights[i] = clip_w

                if debug_printed < DETECT_DEBUG_SAMPLES:
                    signed_txt = "n/a"
                    if nearest_no is not None:
                        vec = garment_in_body - nearest_co
                        signed_txt = f"{float(vec.dot(nearest_no)):.6f}"
                    world_co = garment_mw @ v.co
                    log_debug(
                        bpy.context,
                        "[Cloth Guard][Detect][Sample]",
                        f"idx={i}",
                        f"g_world=({world_co.x:.4f},{world_co.y:.4f},{world_co.z:.4f})",
                        f"g_body=({garment_in_body.x:.4f},{garment_in_body.y:.4f},{garment_in_body.z:.4f})",
                        f"nearest=({nearest_co.x:.4f},{nearest_co.y:.4f},{nearest_co.z:.4f})",
                        f"dist={float(dist):.6f}",
                        f"signed={signed_txt}",
                    )
                    debug_printed += 1

        # Face overlap heuristic (gated). BVHTree.overlap can be noisy on dense meshes, so we
        # only escalate to clipping when a secondary penetration check agrees.
        garment_mesh.calc_loop_triangles()
        garment_verts_body = [garment_to_body @ v.co for v in garment_mesh.vertices]
        garment_tris = [tuple(lt.vertices) for lt in garment_mesh.loop_triangles]
        bvh_garment_local = BVHTree.FromPolygons(garment_verts_body, garment_tris, all_triangles=True, epsilon=0.0)

        overlaps = bvh_body.overlap(bvh_garment_local)
        if overlaps:
            for _, g_tri_idx in overlaps:
                g_idx = int(g_tri_idx)
                if g_idx >= len(garment_mesh.loop_triangles):
                    continue
                tri = garment_mesh.loop_triangles[g_idx]
                # Only usable for base-mesh mapping when indices are in range.
                if any(int(vi) >= base_vert_count for vi in tri.vertices):
                    continue

                centroid = (garment_verts_body[int(tri.vertices[0])] + garment_verts_body[int(tri.vertices[1])] + garment_verts_body[int(tri.vertices[2])]) / 3.0
                nearest = bvh_body.find_nearest(centroid)
                if nearest is None:
                    continue
                nearest_co, nearest_no, _, dist = nearest
                if dist is None:
                    dist = (centroid - nearest_co).length
                dist = float(dist)

                penetrates = False
                if nearest_no is not None:
                    vec = centroid - nearest_co
                    if float(vec.dot(nearest_no * body_normal_sign)) < -penetration_eps:
                        penetrates = True
                if not penetrates and dist <= (offset_distance * OVERLAP_NEAR_SURFACE_FRACTION):
                    penetrates = True
                if not penetrates:
                    continue

                for vi in tri.vertices:
                    idx = int(vi)
                    if risk_idx is not None and _vertex_weight(base_mesh.vertices[idx], risk_idx) <= 0.0:
                        continue
                    if boundary_mask[idx]:
                        # Avoid escalating borders unless they are already strongly penetrating.
                        if clipping_weights[idx] < OVERLAP_BORDER_MIN_WEIGHT:
                            continue
                    # Escalate: intersection-like evidence.
                    clipping_weights[idx] = max(clipping_weights[idx], 1.0)
                    contact_weights[idx] = max(contact_weights[idx], 1.0)

        flagged_contact = sum(1 for w in contact_weights if w > 0.0)
        flagged_clipping = sum(1 for w in clipping_weights if w > 0.0)
        flagged_dists = [d for i, d in enumerate(nearest_distances) if d is not None and clipping_weights[i] > 0.0]
        # True depth below the body surface. (offset_distance - dist was "how far inside the
        # offset zone": it reported 0 for any vertex deeper than offset_distance.)
        max_pen = None
        for i, s in enumerate(signed_distances):
            if s is None or clipping_weights[i] <= 0.0:
                continue
            pen = max(0.0, -s)
            max_pen = pen if max_pen is None else max(max_pen, pen)
        stats = ClipDetectionStats(
            checked_verts=checked,
            candidates_within_radius=candidates,
            flagged_contact=flagged_contact,
            flagged_clipping=flagged_clipping,
            min_nearest_distance=_safe_min(nearest_distances),
            avg_flagged_distance=_safe_avg(flagged_dists),
            max_penetration=max_pen,
        )
        return ClipDetectionResult(
            stats=stats,
            contact_weights=contact_weights,
            clipping_weights=clipping_weights,
            nearest_distances=nearest_distances,
        )
    finally:
        garment_eval.to_mesh_clear()


def compute_proximity_weights(
    *,
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    depsgraph,
    offset_distance: float,
    detection_radius: float,
    correction_strength: float,
    use_risk_area: bool,
    preserve_pinned_areas: bool,
) -> ProximityWeightsResult:
    """
    Compute per-vertex weights based on closest distance to the body surface.

    Weight behavior:
    - dist <= offset_distance: 1.0
    - dist >= detection_radius: 0.0
    - in-between: linear falloff

    Notes:
    - This is a proximity weighting pass only. The actual deformation is
      performed by modifiers (Shrinkwrap + optional smoothing).
    """
    if detection_radius <= 0.0:
        detection_radius = offset_distance
    if detection_radius < offset_distance:
        detection_radius = offset_distance

    garment_eval = garment_obj.evaluated_get(depsgraph)
    body_eval = body_obj.evaluated_get(depsgraph)

    bvh_body = build_bvh(body_eval, depsgraph)

    mesh_eval = garment_eval.to_mesh()
    try:
        g_mw = garment_eval.matrix_world

        risk_idx = _vertex_group_index(garment_obj, CG_VG_RISK) if use_risk_area else None
        pinned_idx = _vertex_group_index(garment_obj, CG_VG_PINNED) if preserve_pinned_areas else None
        collar_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_COLLAR)
        hem_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_HEM)
        seams_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_SEAMS)

        weights: list[float] = [0.0] * len(mesh_eval.vertices)
        affected = 0

        # Preserve groups reduce intensity (MVP: fixed scaling).
        preserve_scale = CORRECTION_PRESERVE_SCALE

        # For group weight lookup, we read from the original mesh (stable indexing).
        base_mesh = garment_obj.data

        for i, v in enumerate(mesh_eval.vertices):
            if risk_idx is not None:
                risk_w = _vertex_weight(base_mesh.vertices[i], risk_idx)
                if risk_w <= 0.0:
                    continue

            world_co = g_mw @ v.co
            nearest = bvh_body.find_nearest(world_co)
            if nearest is None:
                continue

            nearest_co, _, _, dist = nearest
            if dist is None:
                dist = (world_co - nearest_co).length

            if dist >= detection_radius:
                continue

            if dist <= offset_distance or detection_radius == offset_distance:
                base = 1.0
            else:
                base = (detection_radius - dist) / (detection_radius - offset_distance)

            w = max(0.0, min(1.0, base)) * float(correction_strength)

            if pinned_idx is not None:
                pinned_w = _vertex_weight(base_mesh.vertices[i], pinned_idx)
                if pinned_w > 0.0:
                    w *= max(0.0, 1.0 - pinned_w)

            preserve_w = 0.0
            if collar_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], collar_idx))
            if hem_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], hem_idx))
            if seams_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], seams_idx))

            if preserve_w > 0.0:
                w *= max(0.0, 1.0 - preserve_w * preserve_scale)

            if w > 0.0:
                affected += 1
                weights[i] = w
    finally:
        body_eval.to_mesh_clear()
        garment_eval.to_mesh_clear()

    return ProximityWeightsResult(weights=weights, affected_count=affected)


@dataclass(frozen=True)
class SelfClipStats:
    checked_verts: int
    flagged_verts: int
    min_distance: float | None


def detect_self_clipping(
    *,
    garment_obj: bpy.types.Object,
    depsgraph,
    radius: float,
    ignore_neighbor_rings: int = 2,
) -> tuple[SelfClipStats, list[float]]:
    """
    Experimental: detect suspected self-clipping on a topology-stable evaluated mesh.

    Heuristic:
    - For each vertex, query nearest point on the same mesh BVH at slight offsets along +/- normal.
    - Ignore hits on faces that include the vertex or its nearby neighbors (ignore rings).
    - Flag vertices where a non-adjacent surface is within the given radius.
    """
    radius = max(0.0, float(radius))
    obj_eval = garment_obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()
    try:
        base_mesh = garment_obj.data
        if len(mesh_eval.vertices) != len(base_mesh.vertices):
            raise RuntimeError("Self-clipping detection requires a topology-stable cage evaluation")

        mesh_eval.calc_loop_triangles()
        verts = [v.co.copy() for v in mesh_eval.vertices]
        tris = [tuple(lt.vertices) for lt in mesh_eval.loop_triangles]
        bvh = BVHTree.FromPolygons(verts, tris, all_triangles=True, epsilon=0.0)

        adjacency = _build_vertex_adjacency(base_mesh)
        ignore_sets: list[set[int]] = [
            _neighbor_rings(adjacency, i, int(ignore_neighbor_rings)) for i in range(len(base_mesh.vertices))
        ]

        weights = [0.0] * len(base_mesh.vertices)
        checked = 0
        flagged = 0
        min_d = None

        eps = max(1e-6, radius * SELF_CLIP_PROBE_FRACTION)

        for i, v in enumerate(mesh_eval.vertices):
            checked += 1
            if radius <= 0.0:
                continue

            n = v.normal.normalized() if v.normal.length > 1e-12 else Vector((0.0, 0.0, 1.0))
            ignore = ignore_sets[i]

            hit_any = False
            best_dist = None
            for sign in (1.0, -1.0):
                p = v.co + n * (eps * sign)
                hit = bvh.find_nearest(p)
                if hit is None:
                    continue
                _, _, face_index, dist = hit
                if face_index is None:
                    continue
                face_index = int(face_index)
                tri = tris[face_index]
                if any(int(vi) in ignore for vi in tri):
                    continue
                d = float(dist) if dist is not None else float((p - hit[0]).length)
                if best_dist is None or d < best_dist:
                    best_dist = d
                if d <= radius:
                    hit_any = True

            if best_dist is not None:
                min_d = best_dist if min_d is None else min(min_d, best_dist)
            if not hit_any:
                continue

            weights[i] = 1.0
            flagged += 1

        stats = SelfClipStats(checked_verts=checked, flagged_verts=flagged, min_distance=min_d)
        return stats, weights
    finally:
        obj_eval.to_mesh_clear()
