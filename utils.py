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

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

CG_VG_BODY_MASK = "CG_BodyMask"
CG_VG_CONTACT = "CG_Contact"
CG_VG_CLIPPING = "CG_Clipping"

CG_VG_RISK = "CG_RiskArea"
CG_VG_PINNED = "CG_Pinned"
CG_VG_PRESERVE_COLLAR = "CG_Preserve_Collar"
CG_VG_PRESERVE_HEM = "CG_Preserve_Hem"
CG_VG_PRESERVE_SEAMS = "CG_Preserve_Seams"

CG_MOD_BODY_MASK = "CG_BodyMask"
CG_MOD_ANTICLIP = "CG_AntiClip"
CG_MOD_SMOOTH = "CG_Smooth"
CG_MOD_SHAPE_PRESERVE = "CG_ShapePreserve"

CG_SHAPEKEY_LIVE = "CG_LiveCorrection"
CG_SHAPEKEY_LIVE_PRESERVE = "CG_LivePreserve"
CG_SHAPEKEY_REST = "CG_RestShape"
CG_VG_SHAPE_DRIFT = "CG_ShapeDrift"
CG_VG_SHAPE_PRESERVE = "CG_ShapePreserve"


def is_mesh_object(obj) -> bool:
    return obj is not None and getattr(obj, "type", None) == "MESH"


def ensure_vertex_group(obj: bpy.types.Object, name: str) -> bpy.types.VertexGroup:
    vg = obj.vertex_groups.get(name)
    if vg is None:
        vg = obj.vertex_groups.new(name=name)
    return vg


def clear_vertex_group(obj: bpy.types.Object, vg: bpy.types.VertexGroup) -> None:
    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "vertices"):
        return
    count = len(mesh.vertices)
    if count == 0:
        return
    indices = list(range(count))
    try:
        vg.remove(indices)
    except RuntimeError:
        for i in indices:
            try:
                vg.remove([i])
            except Exception:
                pass


def _vertex_group_index(obj: bpy.types.Object, group_name: str) -> Optional[int]:
    vg = obj.vertex_groups.get(group_name)
    return vg.index if vg is not None else None


def _vertex_weight(v: bpy.types.MeshVertex, group_index: int) -> float:
    for g in v.groups:
        if g.group == group_index:
            return float(g.weight)
    return 0.0


def build_bvh(obj_eval: bpy.types.Object, depsgraph) -> BVHTree:
    return BVHTree.FromObject(obj_eval, depsgraph, epsilon=0.0)


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


@dataclass(frozen=True)
class ClipDetectionResult:
    stats: ClipDetectionStats
    contact_weights: list[float]
    clipping_weights: list[float]
    nearest_distances: list[float | None]


def _boundary_vertex_mask(mesh: bpy.types.Mesh) -> list[bool]:
    """
    Return a boolean mask of boundary vertices for the given base mesh.

    We intentionally avoid relying on MeshEdge.is_boundary (not present in all
    Blender versions) and compute boundary edges by counting polygon edge usage.
    """
    boundary = [False] * len(mesh.vertices)
    if len(mesh.vertices) == 0:
        return boundary

    edge_use: dict[tuple[int, int], int] = {}
    for poly in mesh.polygons:
        for a, b in poly.edge_keys:
            key = (int(a), int(b))
            edge_use[key] = edge_use.get(key, 0) + 1

    for (a, b), count in edge_use.items():
        if count == 1:
            boundary[a] = True
            boundary[b] = True
    return boundary


def _safe_min(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return min(vals) if vals else None


def _safe_avg(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _ensure_shape_keys(obj: bpy.types.Object) -> bpy.types.Key:
    if obj.data.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)
    return obj.data.shape_keys


def ensure_live_correction_shapekey(garment_obj: bpy.types.Object) -> bpy.types.ShapeKey:
    keys = _ensure_shape_keys(garment_obj)
    kb = keys.key_blocks.get(CG_SHAPEKEY_LIVE)
    if kb is None:
        kb = garment_obj.shape_key_add(name=CG_SHAPEKEY_LIVE, from_mix=False)
    return kb


def ensure_rest_shape_shapekey(garment_obj: bpy.types.Object) -> bpy.types.ShapeKey:
    keys = _ensure_shape_keys(garment_obj)
    kb = keys.key_blocks.get(CG_SHAPEKEY_REST)
    if kb is None:
        kb = garment_obj.shape_key_add(name=CG_SHAPEKEY_REST, from_mix=False)
        kb.value = 0.0
        kb.mute = True
    return kb


def ensure_live_preserve_shapekey(garment_obj: bpy.types.Object) -> bpy.types.ShapeKey:
    keys = _ensure_shape_keys(garment_obj)
    kb = keys.key_blocks.get(CG_SHAPEKEY_LIVE_PRESERVE)
    if kb is None:
        kb = garment_obj.shape_key_add(name=CG_SHAPEKEY_LIVE_PRESERVE, from_mix=False)
    return kb


def _build_vertex_adjacency(mesh: bpy.types.Mesh) -> list[list[int]]:
    adj: list[list[int]] = [[] for _ in range(len(mesh.vertices))]
    for e in mesh.edges:
        a = int(e.vertices[0])
        b = int(e.vertices[1])
        adj[a].append(b)
        adj[b].append(a)
    return adj


def _smooth_deltas(
    *,
    deltas: list[Vector],
    adjacency: list[list[int]],
    iterations: int,
    strength: float,
) -> list[Vector]:
    if iterations <= 0 or strength <= 0.0:
        return deltas
    strength = max(0.0, min(1.0, float(strength)))

    cur = deltas
    for _ in range(int(iterations)):
        nxt = [v.copy() for v in cur]
        for i, nbrs in enumerate(adjacency):
            if not nbrs:
                continue
            avg = Vector((0.0, 0.0, 0.0))
            for j in nbrs:
                avg += cur[j]
            avg /= float(len(nbrs))
            nxt[i] = cur[i].lerp(avg, strength)
        cur = nxt
    return cur


def _smooth_deltas_structural(
    *,
    deltas: list[Vector],
    adjacency: list[list[int]],
    rest_normals: list[Vector],
    drift_threshold: float,
    iterations: int,
    strength: float,
    normal_cos_limit: float = 0.5,
) -> list[Vector]:
    """
    Structure-aware smoothing for drift fields.

    - Avoids smoothing across sharp normal changes (collars/edges) using a cosine limit.
    - Avoids removing large-scale deformation by down-weighting edges where drift differs a lot.
    """
    if iterations <= 0 or strength <= 0.0:
        return deltas
    strength = max(0.0, min(1.0, float(strength)))
    drift_threshold = max(1e-8, float(drift_threshold))

    # Pre-normalize normals.
    rn = []
    for n in rest_normals:
        rn.append(n.normalized() if n.length > 1e-12 else Vector((0.0, 0.0, 1.0)))

    cur = deltas
    for _ in range(int(iterations)):
        nxt = [v.copy() for v in cur]
        for i, nbrs in enumerate(adjacency):
            if not nbrs:
                continue
            acc = Vector((0.0, 0.0, 0.0))
            wsum = 0.0
            ni = rn[i]
            for j in nbrs:
                nj = rn[j]
                nd = float(ni.dot(nj))
                if nd < normal_cos_limit:
                    continue
                # Drift similarity: if drift varies a lot across an edge, treat it as large-scale deformation and smooth less.
                dd = float((cur[j] - cur[i]).length)
                sim = max(0.0, 1.0 - (dd / (drift_threshold * 4.0)))
                w = nd * sim
                if w <= 0.0:
                    continue
                acc += cur[j] * w
                wsum += w
            if wsum > 1e-12:
                avg = acc / wsum
                nxt[i] = cur[i].lerp(avg, strength)
        cur = nxt
    return cur


def detect_clipping(
    *,
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    depsgraph,
    offset_distance: float,
    detection_radius: float,
    use_risk_area: bool,
) -> ClipDetectionResult:
    """
    Robust MVP clipping detection on evaluated (deformed) meshes:
    - Proximity candidates via nearest surface distance (body-local BVH queries)
    - Contact weights for near-body vertices (CG_Contact)
    - Clipping weights for likely penetration (CG_Clipping), using surface normals (dot < 0)
    - Optional overlap heuristic, gated by a secondary penetration check (reduces false positives)
    """
    offset_distance = max(0.0, float(offset_distance))
    detection_radius = max(offset_distance, float(detection_radius))

    garment_eval = garment_obj.evaluated_get(depsgraph)
    body_eval = body_obj.evaluated_get(depsgraph)

    body_mesh = body_eval.to_mesh()
    garment_mesh = garment_eval.to_mesh()
    try:
        base_mesh = garment_obj.data
        base_vert_count = len(base_mesh.vertices)
        vertex_count_matches = len(garment_mesh.vertices) == base_vert_count
        if not vertex_count_matches:
            print(
                "[Cloth Guard][Detect] WARNING: evaluated garment vertex count differs from base mesh. "
                "Topology-changing modifiers are likely enabled; CG_Clipping mapping to the base mesh may be incomplete."
            )

        body_mw = body_eval.matrix_world
        garment_mw = garment_eval.matrix_world
        garment_to_body = body_mw.inverted_safe() @ garment_mw

        # BVH is built in evaluated BODY LOCAL SPACE.
        body_mesh.calc_loop_triangles()
        body_verts = [v.co.copy() for v in body_mesh.vertices]
        body_tris = [tuple(lt.vertices) for lt in body_mesh.loop_triangles]
        bvh_body = BVHTree.FromPolygons(body_verts, body_tris, all_triangles=True, epsilon=0.0)
        risk_idx = _vertex_group_index(garment_obj, CG_VG_RISK) if use_risk_area else None

        collar_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_COLLAR)
        hem_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_HEM)
        seams_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_SEAMS)
        preserve_scale_contact = 0.75
        preserve_scale_clipping = 0.25

        # Boundary attenuation helps avoid noisy selections on open edges (collars/hem boundaries).
        boundary_mask = _boundary_vertex_mask(base_mesh)
        # Strongly protect open borders/silhouette edges from false positives.
        boundary_contact_scale = 0.1
        boundary_clipping_scale = 0.25

        # When normals are noisy or inconsistent, require a small negative dot to call it penetration.
        penetration_eps = max(1e-6, offset_distance * 0.05)

        contact_weights: list[float] = [0.0] * base_vert_count
        clipping_weights: list[float] = [0.0] * base_vert_count
        nearest_distances: list[float | None] = [None] * base_vert_count

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
            if dist is None:
                dist = (garment_in_body - nearest_co).length
            dist = float(dist)
            nearest_distances[i] = dist

            if dist <= detection_radius:
                candidates += 1
                if detection_radius == offset_distance:
                    contact_w = 1.0
                elif dist <= offset_distance:
                    contact_w = 1.0
                else:
                    contact_w = (detection_radius - dist) / (detection_radius - offset_distance)
                    contact_w = max(0.0, min(1.0, float(contact_w)))

                clip_w = 0.0
                if nearest_no is not None:
                    vec = garment_in_body - nearest_co
                    signed = float(vec.dot(nearest_no))
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

                if debug_printed < 3:
                    signed_txt = "n/a"
                    if nearest_no is not None:
                        vec = garment_in_body - nearest_co
                        signed_txt = f"{float(vec.dot(nearest_no)):.6f}"
                    world_co = garment_mw @ v.co
                    print(
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
                    if float(vec.dot(nearest_no)) < -penetration_eps:
                        penetrates = True
                if not penetrates and dist <= (offset_distance * 0.15):
                    penetrates = True
                if not penetrates:
                    continue

                for vi in tri.vertices:
                    idx = int(vi)
                    if risk_idx is not None and _vertex_weight(base_mesh.vertices[idx], risk_idx) <= 0.0:
                        continue
                    if boundary_mask[idx]:
                        # Avoid escalating borders unless they are already strongly penetrating.
                        if clipping_weights[idx] < 0.9:
                            continue
                    # Escalate: intersection-like evidence.
                    clipping_weights[idx] = max(clipping_weights[idx], 1.0)
                    contact_weights[idx] = max(contact_weights[idx], 1.0)

        flagged_contact = sum(1 for w in contact_weights if w > 0.0)
        flagged_clipping = sum(1 for w in clipping_weights if w > 0.0)
        flagged_dists = [d for i, d in enumerate(nearest_distances) if d is not None and clipping_weights[i] > 0.0]
        stats = ClipDetectionStats(
            checked_verts=checked,
            candidates_within_radius=candidates,
            flagged_contact=flagged_contact,
            flagged_clipping=flagged_clipping,
            min_nearest_distance=_safe_min(nearest_distances),
            avg_flagged_distance=_safe_avg(flagged_dists),
        )
        return ClipDetectionResult(
            stats=stats,
            contact_weights=contact_weights,
            clipping_weights=clipping_weights,
            nearest_distances=nearest_distances,
        )
    finally:
        body_eval.to_mesh_clear()
        garment_eval.to_mesh_clear()


@dataclass(frozen=True)
class PoseCorrectionStats:
    checked_verts: int
    corrected_verts: int
    min_nearest_distance: float | None
    changed_verts: int
    max_delta_distance: float


def correct_current_pose(
    *,
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    depsgraph,
    offset_distance: float,
    detection_radius: float,
    correction_strength: float,
    max_push_distance: float,
    smooth_iterations: int,
    smooth_strength: float,
    use_risk_area: bool,
    preserve_pinned_areas: bool,
    accumulate: bool = False,
) -> PoseCorrectionStats:
    offset_distance = max(0.0, float(offset_distance))
    detection_radius = max(offset_distance, float(detection_radius))
    correction_strength = max(0.0, min(1.0, float(correction_strength)))
    max_push_distance = max(0.0, float(max_push_distance))

    garment_eval = garment_obj.evaluated_get(depsgraph)
    body_eval = body_obj.evaluated_get(depsgraph)

    body_mesh = body_eval.to_mesh()
    garment_mesh = garment_eval.to_mesh()
    try:
        base_mesh = garment_obj.data
        if len(garment_mesh.vertices) != len(base_mesh.vertices):
            raise RuntimeError(
                "Garment evaluated mesh vertex count differs from base mesh. "
                "For MVP correction, keep the garment modifier stack topology-stable (Armature/shape keys are OK; Subdivision/Remesh are not)."
            )

        body_mw = body_eval.matrix_world
        garment_mw = garment_eval.matrix_world
        garment_to_body = body_mw.inverted_safe() @ garment_mw

        # BVH is built in evaluated BODY LOCAL SPACE.
        body_mesh.calc_loop_triangles()
        body_verts = [v.co.copy() for v in body_mesh.vertices]
        body_tris = [tuple(lt.vertices) for lt in body_mesh.loop_triangles]
        bvh_body = BVHTree.FromPolygons(body_verts, body_tris, all_triangles=True, epsilon=0.0)

        inv_garment_world = garment_obj.matrix_world.inverted_safe()

        risk_idx = _vertex_group_index(garment_obj, CG_VG_RISK) if use_risk_area else None
        pinned_idx = _vertex_group_index(garment_obj, CG_VG_PINNED) if preserve_pinned_areas else None
        collar_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_COLLAR)
        hem_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_HEM)
        seams_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_SEAMS)

        boundary_mask = _boundary_vertex_mask(base_mesh)
        boundary_scale = 0.25

        preserve_scale = 0.5
        penetration_eps = max(1e-6, offset_distance * 0.05)

        deltas: list[Vector] = [Vector((0.0, 0.0, 0.0)) for _ in range(len(garment_mesh.vertices))]
        checked = 0
        corrected = 0
        nearest_distances: list[float | None] = [None] * len(garment_mesh.vertices)

        def _choose_outward_normal(point_body: Vector, normal_body: Vector) -> Vector:
            n = normal_body.normalized()
            # Pick the direction that increases distance from body most (handles flipped normals).
            test_step = max(1e-6, offset_distance * 0.25)
            d_pos = bvh_body.find_nearest(point_body + n * test_step)
            d_neg = bvh_body.find_nearest(point_body - n * test_step)
            dist_pos = float(d_pos[3]) if d_pos is not None and d_pos[3] is not None else 0.0
            dist_neg = float(d_neg[3]) if d_neg is not None and d_neg[3] is not None else 0.0
            return n if dist_pos >= dist_neg else (-n)

        for i, v in enumerate(garment_mesh.vertices):
            if risk_idx is not None and _vertex_weight(base_mesh.vertices[i], risk_idx) <= 0.0:
                continue

            checked += 1
            garment_in_body = garment_to_body @ v.co
            nearest = bvh_body.find_nearest(garment_in_body)
            if nearest is None:
                continue
            nearest_co, nearest_no, _, dist = nearest
            if dist is None:
                dist = (garment_in_body - nearest_co).length
            dist = float(dist)
            nearest_distances[i] = dist

            if dist > detection_radius and dist > offset_distance:
                continue

            vec = garment_in_body - nearest_co
            if nearest_no is not None:
                outward_no = _choose_outward_normal(garment_in_body, nearest_no)
                signed = float(vec.dot(outward_no))
                inside = signed < -penetration_eps
                push_dir_body = outward_no
            else:
                signed = float(vec.length)
                inside = False
                push_dir_body = vec.normalized() if vec.length > 1e-12 else Vector((0.0, 0.0, 1.0))

            if inside:
                push_amount = offset_distance + (-signed)
            else:
                if dist >= offset_distance:
                    continue
                push_amount = offset_distance - dist

            if max_push_distance > 0.0:
                push_amount = min(push_amount, max_push_distance)

            push_amount *= correction_strength
            if not inside and offset_distance > 1e-8:
                # Softer influence for "near contact" (outside) compared to penetration.
                tight = max(0.0, min(1.0, (offset_distance - dist) / offset_distance))
                push_amount *= tight
            if push_amount <= 0.0:
                continue

            # Group-based attenuation.
            scale = 1.0
            if pinned_idx is not None:
                pinned_w = _vertex_weight(base_mesh.vertices[i], pinned_idx)
                if pinned_w > 0.0:
                    scale *= max(0.0, 1.0 - pinned_w)

            preserve_w = 0.0
            if collar_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], collar_idx))
            if hem_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], hem_idx))
            if seams_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], seams_idx))
            if preserve_w > 0.0:
                scale *= max(0.0, 1.0 - preserve_w * preserve_scale)

            if boundary_mask[i]:
                scale *= boundary_scale

            if scale <= 0.0:
                continue

            push_dir_world = (body_mw.to_3x3() @ push_dir_body).normalized()
            world_delta = push_dir_world * (push_amount * scale)
            local_delta = inv_garment_world.to_3x3() @ world_delta
            deltas[i] = local_delta
            corrected += 1

        # Smooth deltas over the garment topology.
        deltas = _smooth_deltas(
            deltas=deltas,
            adjacency=_build_vertex_adjacency(base_mesh),
            iterations=int(smooth_iterations),
            strength=float(smooth_strength),
        )

        # Compute delta stats after smoothing (what will be written to the shape key).
        changed = 0
        max_delta = 0.0
        mw3 = garment_obj.matrix_world.to_3x3()
        for d in deltas:
            ln = float(d.length)
            if ln > 1e-12:
                changed += 1
                wd = mw3 @ d
                max_delta = max(max_delta, float(wd.length))

        live = ensure_live_correction_shapekey(garment_obj)
        basis = garment_obj.data.shape_keys.key_blocks[0]
        base_kb = live if accumulate else basis
        for i in range(len(base_mesh.vertices)):
            live.data[i].co = base_kb.data[i].co + deltas[i]
        live.value = 1.0

        return PoseCorrectionStats(
            checked_verts=checked,
            corrected_verts=corrected,
            min_nearest_distance=_safe_min(nearest_distances),
            changed_verts=changed,
            max_delta_distance=max_delta,
        )
    finally:
        body_eval.to_mesh_clear()
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
        preserve_scale = 0.5

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


def write_weights_to_vertex_group(
    obj: bpy.types.Object, group_name: str, weights: Iterable[float]
) -> int:
    vg = ensure_vertex_group(obj, group_name)
    clear_vertex_group(obj, vg)

    affected = 0
    for i, w in enumerate(weights):
        if w > 0.0:
            vg.add([i], float(w), "REPLACE")
            affected += 1
    return affected


@dataclass(frozen=True)
class ShapeDriftStats:
    checked_verts: int
    flagged_verts: int
    max_drift_distance: float
    avg_flagged_drift: float | None


def _vector_safe_avg(vals: Iterable[float]) -> float | None:
    arr = [float(v) for v in vals]
    return (sum(arr) / len(arr)) if arr else None


def store_rest_shape(
    *,
    garment_obj: bpy.types.Object,
    depsgraph,
    cage_eval_obj: bpy.types.Object | None = None,
) -> int:
    """
    Store the current evaluated (typically clean) garment shape into CG_RestShape (non-destructive).
    Returns number of vertices written.
    """
    if garment_obj.type != "MESH":
        raise RuntimeError("Garment is not a mesh")

    obj_eval = cage_eval_obj if cage_eval_obj is not None else garment_obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()
    try:
        base_mesh = garment_obj.data
        if len(mesh_eval.vertices) != len(base_mesh.vertices):
            raise RuntimeError("Rest shape storage requires a topology-stable cage evaluation")

        kb = ensure_rest_shape_shapekey(garment_obj)
        for i in range(len(base_mesh.vertices)):
            kb.data[i].co = mesh_eval.vertices[i].co
        kb.value = 0.0
        kb.mute = True
        return len(base_mesh.vertices)
    finally:
        obj_eval.to_mesh_clear()


def analyze_shape_drift(
    *,
    garment_obj: bpy.types.Object,
    depsgraph,
    drift_threshold: float,
    cage_eval_obj: bpy.types.Object | None = None,
    protect_borders: bool = True,
    protect_scale: float = 0.5,
) -> tuple[ShapeDriftStats, list[float]]:
    """
    Compare current evaluated cage shape to CG_RestShape and produce per-vertex drift weights.
    Returns (stats, weights) suitable for writing into CG_ShapeDrift.
    """
    if garment_obj.data.shape_keys is None or garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_REST) is None:
        raise RuntimeError("Missing rest shape. Click Store Rest Shape first.")

    rest = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_REST)
    if rest is None:
        raise RuntimeError("Missing rest shape. Click Store Rest Shape first.")

    obj_eval = cage_eval_obj if cage_eval_obj is not None else garment_obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()
    try:
        base_mesh = garment_obj.data
        if len(mesh_eval.vertices) != len(base_mesh.vertices) or len(rest.data) != len(base_mesh.vertices):
            raise RuntimeError("Shape drift analysis requires a topology-stable cage evaluation")

        drift_threshold = max(1e-8, float(drift_threshold))
        boundary_mask = _boundary_vertex_mask(base_mesh) if protect_borders else [False] * len(base_mesh.vertices)

        weights = [0.0] * len(base_mesh.vertices)
        flagged = 0
        max_drift = 0.0
        flagged_drifts: list[float] = []

        for i in range(len(base_mesh.vertices)):
            d = mesh_eval.vertices[i].co - rest.data[i].co
            dist = float(d.length)
            max_drift = max(max_drift, dist)
            if dist <= drift_threshold:
                continue
            w = max(0.0, min(1.0, (dist - drift_threshold) / max(drift_threshold, 1e-8)))
            if boundary_mask[i]:
                w *= float(protect_scale)
            if w > 0.0:
                flagged += 1
                flagged_drifts.append(dist)
                weights[i] = w

        stats = ShapeDriftStats(
            checked_verts=len(base_mesh.vertices),
            flagged_verts=flagged,
            max_drift_distance=max_drift,
            avg_flagged_drift=_vector_safe_avg(flagged_drifts),
        )
        return stats, weights
    finally:
        obj_eval.to_mesh_clear()


def generate_shape_preservation(
    *,
    garment_obj: bpy.types.Object,
    depsgraph,
    strength: float,
    smoothing_iterations: int,
    smoothing_strength: float,
    volume_preservation: float,
    silhouette_preservation: float,
    drift_threshold: float,
    protect_borders: bool,
    protect_groups: bool,
    cage_eval_obj: bpy.types.Object | None = None,
) -> tuple[int, float]:
    """
    Generate/update CG_LivePreserve to reduce high-frequency drift compared to CG_RestShape.
    Returns (changed_verts, max_delta_world_m).
    """
    if garment_obj.type != "MESH":
        raise RuntimeError("Garment is not a mesh")
    if garment_obj.data.shape_keys is None or garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_REST) is None:
        raise RuntimeError("Missing rest shape. Click Store Rest Shape first.")

    strength = max(0.0, min(1.0, float(strength)))
    smoothing_iterations = max(0, int(smoothing_iterations))
    smoothing_strength = max(0.0, min(1.0, float(smoothing_strength)))
    volume_preservation = max(0.0, min(1.0, float(volume_preservation)))
    silhouette_preservation = max(0.0, min(1.0, float(silhouette_preservation)))
    drift_threshold = max(1e-8, float(drift_threshold))

    rest = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_REST)
    if rest is None:
        raise RuntimeError("Missing rest shape. Click Store Rest Shape first.")

    obj_eval = cage_eval_obj if cage_eval_obj is not None else garment_obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()
    try:
        mesh_eval.calc_normals()
        base_mesh = garment_obj.data
        if len(mesh_eval.vertices) != len(base_mesh.vertices) or len(rest.data) != len(base_mesh.vertices):
            raise RuntimeError("Shape preservation requires a topology-stable cage evaluation")

        drift: list[Vector] = [Vector((0.0, 0.0, 0.0)) for _ in range(len(base_mesh.vertices))]
        for i in range(len(base_mesh.vertices)):
            drift[i] = mesh_eval.vertices[i].co - rest.data[i].co

        adjacency = _build_vertex_adjacency(base_mesh)
        rest_normals = [v.normal.copy() for v in base_mesh.vertices]
        smoothed = _smooth_deltas_structural(
            deltas=drift,
            adjacency=adjacency,
            rest_normals=rest_normals,
            drift_threshold=drift_threshold,
            iterations=int(smoothing_iterations),
            strength=float(smoothing_strength),
        )

        boundary_mask = _boundary_vertex_mask(base_mesh) if protect_borders else [False] * len(base_mesh.vertices)
        boundary_scale = 0.25

        pinned_idx = _vertex_group_index(garment_obj, CG_VG_PINNED) if protect_groups else None
        collar_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_COLLAR) if protect_groups else None
        hem_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_HEM) if protect_groups else None
        seams_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_SEAMS) if protect_groups else None
        preserve_scale = 0.75

        live = ensure_live_preserve_shapekey(garment_obj)
        basis = garment_obj.data.shape_keys.key_blocks[0]

        changed = 0
        max_delta_world = 0.0
        mw3 = garment_obj.matrix_world.to_3x3()

        for i in range(len(base_mesh.vertices)):
            corr = (smoothed[i] - drift[i]) * strength

            # Prevent inward collapse: reduce inward normal component relative to the CURRENT pose normal.
            cn = mesh_eval.vertices[i].normal
            if cn.length > 1e-12:
                cn = cn.normalized()
                dotn = float(corr.dot(cn))
                cn_comp = cn * dotn
                ct_comp = corr - cn_comp
                if dotn < 0.0:
                    cn_comp *= float(1.0 - volume_preservation)
                corr = ct_comp + cn_comp

            # Silhouette preservation: additionally reduce normal component in rest space (helps keep thickness/opening).
            rn = base_mesh.vertices[i].normal
            if rn.length > 1e-12:
                rn = rn.normalized()
                dotr = float(corr.dot(rn))
                rn_comp = rn * dotr
                rt_comp = corr - rn_comp
                corr = rt_comp + rn_comp * float(1.0 - silhouette_preservation)

            scale = 1.0
            if pinned_idx is not None:
                pw = _vertex_weight(base_mesh.vertices[i], pinned_idx)
                if pw > 0.0:
                    scale *= max(0.0, 1.0 - pw)

            preserve_w = 0.0
            if collar_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], collar_idx))
            if hem_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], hem_idx))
            if seams_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], seams_idx))
            if preserve_w > 0.0:
                scale *= max(0.0, 1.0 - preserve_w * preserve_scale)

            if boundary_mask[i]:
                scale *= boundary_scale

            corr *= scale

            # Clamp: never remove more than a fraction of the local drift magnitude (avoids global pull/size shrink).
            drift_len = float(drift[i].length)
            corr_len = float(corr.length)
            if drift_len > 1e-8 and corr_len > drift_len * 0.75:
                corr *= (drift_len * 0.75) / max(corr_len, 1e-12)

            live.data[i].co = basis.data[i].co + corr

            ln = float(corr.length)
            if ln > 1e-12:
                changed += 1
                max_delta_world = max(max_delta_world, float((mw3 @ corr).length))

        live.value = 1.0
        return changed, max_delta_world
    finally:
        obj_eval.to_mesh_clear()


def ensure_body_mask_modifier(body_obj: bpy.types.Object) -> bpy.types.MaskModifier:
    mod = body_obj.modifiers.get(CG_MOD_BODY_MASK)
    if mod is None:
        mod = body_obj.modifiers.new(name=CG_MOD_BODY_MASK, type="MASK")
    mod.vertex_group = CG_VG_BODY_MASK
    mod.invert_vertex_group = True
    return mod


def ensure_anticlip_modifiers(
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    *,
    offset_distance: float,
    smooth_iterations: int,
    smooth_strength: float,
) -> tuple[bpy.types.Modifier, bpy.types.Modifier | None]:
    mod_sw = garment_obj.modifiers.get(CG_MOD_ANTICLIP)
    if mod_sw is None:
        mod_sw = garment_obj.modifiers.new(name=CG_MOD_ANTICLIP, type="SHRINKWRAP")
    mod_sw.target = body_obj
    if hasattr(mod_sw, "wrap_method"):
        mod_sw.wrap_method = "NEAREST_SURFACEPOINT"
    if hasattr(mod_sw, "offset"):
        mod_sw.offset = float(offset_distance)
    if hasattr(mod_sw, "vertex_group"):
        mod_sw.vertex_group = CG_VG_CLIPPING
    if hasattr(mod_sw, "use_keep_above_surface"):
        mod_sw.use_keep_above_surface = True

    mod_sm = garment_obj.modifiers.get(CG_MOD_SMOOTH)
    if smooth_iterations > 0 and smooth_strength > 0.0:
        if mod_sm is None:
            mod_sm = garment_obj.modifiers.new(name=CG_MOD_SMOOTH, type="CORRECTIVE_SMOOTH")
        if hasattr(mod_sm, "vertex_group"):
            mod_sm.vertex_group = CG_VG_CLIPPING
        if hasattr(mod_sm, "factor"):
            mod_sm.factor = float(smooth_strength)
        if hasattr(mod_sm, "iterations"):
            mod_sm.iterations = int(smooth_iterations)
    else:
        if mod_sm is not None:
            garment_obj.modifiers.remove(mod_sm)
            mod_sm = None

    return mod_sw, mod_sm


def compute_shape_preserve_mask_weights(
    garment_obj: bpy.types.Object,
    *,
    protect_groups: bool,
) -> list[float]:
    """
    Build a per-vertex mask for shape preservation modifiers.

    This is designed to work even when the garment has topology-changing modifiers
    (weights will be propagated by modifiers like Subdivision).
    """
    base_mesh = garment_obj.data
    weights = [1.0] * len(base_mesh.vertices)

    if not protect_groups:
        return weights

    pinned_idx = _vertex_group_index(garment_obj, CG_VG_PINNED)
    collar_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_COLLAR)
    hem_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_HEM)
    seams_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_SEAMS)

    for i, v in enumerate(base_mesh.vertices):
        scale = 1.0
        if pinned_idx is not None:
            pw = _vertex_weight(v, pinned_idx)
            if pw > 0.0:
                scale *= max(0.0, 1.0 - pw)

        preserve_w = 0.0
        if collar_idx is not None:
            preserve_w = max(preserve_w, _vertex_weight(v, collar_idx))
        if hem_idx is not None:
            preserve_w = max(preserve_w, _vertex_weight(v, hem_idx))
        if seams_idx is not None:
            preserve_w = max(preserve_w, _vertex_weight(v, seams_idx))
        if preserve_w > 0.0:
            # Strongly protect these regions.
            scale *= max(0.0, 1.0 - preserve_w * 0.9)

        weights[i] *= scale

    return weights


def ensure_shape_preserve_modifier(
    garment_obj: bpy.types.Object,
    *,
    iterations: int,
    factor: float,
) -> bpy.types.CorrectiveSmoothModifier:
    """
    Ensure a Corrective Smooth modifier used for post-stack shape preservation.

    This modifier is topology-safe and works after Subdivision/Geometry Nodes, etc.
    Bind it on a clean frame (Store Rest Shape) so it uses that as the reference.
    """
    mod = garment_obj.modifiers.get(CG_MOD_SHAPE_PRESERVE)
    if mod is None:
        mod = garment_obj.modifiers.new(name=CG_MOD_SHAPE_PRESERVE, type="CORRECTIVE_SMOOTH")

    if hasattr(mod, "iterations"):
        mod.iterations = int(max(0, iterations))
    if hasattr(mod, "factor"):
        mod.factor = float(max(0.0, min(1.0, factor)))
    if hasattr(mod, "use_only_smooth"):
        mod.use_only_smooth = True
    if hasattr(mod, "scale"):
        mod.scale = 1.0

    return mod


def cg_update_modifier_visibility(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    settings = getattr(scene, "cg_settings", None)
    if settings is None:
        return

    garments = []
    # Preferred: explicit garment list.
    for item in getattr(settings, "garments", []):
        obj = getattr(item, "object", None)
        if not getattr(item, "enabled", True):
            continue
        if is_mesh_object(obj):
            garments.append(obj)

    # Backwards-compatible fallbacks.
    if not garments:
        coll = getattr(settings, "garment_collection", None)
        if coll is not None:
            for obj in coll.all_objects:
                if is_mesh_object(obj):
                    garments.append(obj)
    if not garments:
        obj = getattr(settings, "garment_object", None)
        if is_mesh_object(obj):
            garments = [obj]

    if not garments:
        return

    enabled = bool(settings.enable_live_anti_clip)
    for garment_obj in garments:
        if garment_obj.data.shape_keys is not None:
            kb = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_LIVE)
            if kb is not None:
                kb.value = 1.0 if enabled else 0.0
            kb2 = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_LIVE_PRESERVE)
            if kb2 is not None:
                kb2.value = 1.0 if enabled else 0.0

        # Legacy modifier-based workflow (kept for backwards compatibility if present).
        for mod_name in (CG_MOD_ANTICLIP, CG_MOD_SMOOTH, CG_MOD_SHAPE_PRESERVE):
            mod = garment_obj.modifiers.get(mod_name)
            if mod is None:
                continue
            mod.show_viewport = enabled
            mod.show_render = enabled


def add_shapekey_driver_rotation_range(
    *,
    garment_obj: bpy.types.Object,
    shapekey_name: str,
    armature_obj: bpy.types.Object,
    bone_name: str,
    axis: str,
    min_angle_rad: float,
    max_angle_rad: float,
) -> None:
    if garment_obj.data.shape_keys is None:
        raise RuntimeError("Garment has no shape keys")
    kb = garment_obj.data.shape_keys.key_blocks.get(shapekey_name)
    if kb is None:
        raise RuntimeError(f"Shape key not found: {shapekey_name}")
    if armature_obj is None or armature_obj.type != "ARMATURE":
        raise RuntimeError("Armature object is missing or not an Armature")
    if bone_name not in armature_obj.pose.bones:
        raise RuntimeError(f"Bone not found on armature: {bone_name}")

    fcurve = kb.driver_add("value")
    drv = fcurve.driver
    drv.type = "SCRIPTED"

    var = drv.variables.new()
    var.name = "r"
    var.type = "TRANSFORMS"
    tgt = var.targets[0]
    tgt.id = armature_obj
    tgt.bone_target = bone_name
    tgt.transform_space = "LOCAL_SPACE"
    if axis == "X":
        tgt.transform_type = "ROT_X"
    elif axis == "Y":
        tgt.transform_type = "ROT_Y"
    else:
        tgt.transform_type = "ROT_Z"

    if math.isclose(max_angle_rad, min_angle_rad, abs_tol=1e-8):
        drv.expression = "0.0"
        return

    min_a = float(min_angle_rad)
    max_a = float(max_angle_rad)
    denom = max_a - min_a
    drv.expression = f"max(0.0, min(1.0, (r - ({min_a})) / ({denom})))"
