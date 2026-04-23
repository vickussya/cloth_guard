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
CG_VG_CLIPPING = "CG_Clipping"

CG_VG_RISK = "CG_RiskArea"
CG_VG_PINNED = "CG_Pinned"
CG_VG_PRESERVE_COLLAR = "CG_Preserve_Collar"
CG_VG_PRESERVE_HEM = "CG_Preserve_Hem"
CG_VG_PRESERVE_SEAMS = "CG_Preserve_Seams"

CG_MOD_BODY_MASK = "CG_BodyMask"
CG_MOD_ANTICLIP = "CG_AntiClip"
CG_MOD_SMOOTH = "CG_Smooth"

CG_SHAPEKEY_LIVE = "CG_LiveCorrection"


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
    flagged_clipping: int
    min_nearest_distance: float | None
    avg_flagged_distance: float | None


@dataclass(frozen=True)
class ClipDetectionResult:
    stats: ClipDetectionStats
    clipping_indices: list[int]
    candidate_indices: list[int]
    nearest_distances: list[float | None]


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
    - Penetration heuristic using nearest surface normal (dot < 0)
    - Face overlap heuristic via BVH overlap (catches face intersections even if no vertex is inside)
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

        # When normals are noisy or inconsistent, require a small negative dot to call it penetration.
        penetration_eps = max(1e-6, offset_distance * 0.05)

        nearest_distances: list[float | None] = [None] * len(garment_mesh.vertices)
        candidate_indices: list[int] = []
        clipping_set: set[int] = set()

        checked = 0
        candidates = 0
        debug_printed = 0

        for i, v in enumerate(garment_mesh.vertices):
            if risk_idx is not None and vertex_count_matches:
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
            nearest_distances[i] = float(dist)

            if dist <= detection_radius:
                candidates += 1
                candidate_indices.append(i)

                is_clipping = dist <= offset_distance
                if not is_clipping and nearest_no is not None:
                    vec = garment_in_body - nearest_co
                    # dot < 0 means vertex lies "inside" the body if body normals point outward.
                    if vec.dot(nearest_no) < -penetration_eps:
                        is_clipping = True

                if is_clipping:
                    if i < base_vert_count:
                        clipping_set.add(i)

                if debug_printed < 3:
                    world_co = garment_mw @ v.co
                    print(
                        "[Cloth Guard][Detect][Sample]",
                        f"idx={i}",
                        f"g_world=({world_co.x:.4f},{world_co.y:.4f},{world_co.z:.4f})",
                        f"g_body=({garment_in_body.x:.4f},{garment_in_body.y:.4f},{garment_in_body.z:.4f})",
                        f"nearest=({nearest_co.x:.4f},{nearest_co.y:.4f},{nearest_co.z:.4f})",
                        f"dist={float(dist):.6f}",
                    )
                    debug_printed += 1

        # Face overlap heuristic.
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
                for vi in garment_mesh.loop_triangles[g_idx].vertices:
                    idx = int(vi)
                    if risk_idx is not None and vertex_count_matches and _vertex_weight(base_mesh.vertices[idx], risk_idx) <= 0.0:
                        continue
                    if idx < base_vert_count:
                        clipping_set.add(idx)

        clipping_indices = sorted(clipping_set)
        flagged_dists = [nearest_distances[i] for i in clipping_indices]
        stats = ClipDetectionStats(
            checked_verts=checked,
            candidates_within_radius=candidates,
            flagged_clipping=len(clipping_indices),
            min_nearest_distance=_safe_min(nearest_distances),
            avg_flagged_distance=_safe_avg(flagged_dists),
        )
        return ClipDetectionResult(
            stats=stats,
            clipping_indices=clipping_indices,
            candidate_indices=candidate_indices,
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

        preserve_scale = 0.5
        penetration_eps = max(1e-6, offset_distance * 0.05)

        deltas: list[Vector] = [Vector((0.0, 0.0, 0.0)) for _ in range(len(garment_mesh.vertices))]
        checked = 0
        corrected = 0
        nearest_distances: list[float | None] = [None] * len(garment_mesh.vertices)

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
            dot = vec.dot(nearest_no) if nearest_no is not None else vec.length
            inside = nearest_no is not None and dot < -penetration_eps

            if inside:
                push_amount = offset_distance + (-dot)
                push_dir_body = nearest_no
            else:
                if dist >= offset_distance:
                    continue
                push_amount = offset_distance - dist
                push_dir_body = vec.normalized() if vec.length > 1e-12 else (nearest_no if nearest_no is not None else Vector((0.0, 0.0, 1.0)))

            if max_push_distance > 0.0:
                push_amount = min(push_amount, max_push_distance)

            push_amount *= correction_strength
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

        live = ensure_live_correction_shapekey(garment_obj)
        basis = garment_obj.data.shape_keys.key_blocks[0]
        for i in range(len(base_mesh.vertices)):
            live.data[i].co = basis.data[i].co + deltas[i]

        return PoseCorrectionStats(
            checked_verts=checked,
            corrected_verts=corrected,
            min_nearest_distance=_safe_min(nearest_distances),
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


def cg_update_modifier_visibility(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    settings = getattr(scene, "cg_settings", None)
    if settings is None:
        return

    garments = []
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

        # Legacy modifier-based workflow (kept for backwards compatibility if present).
        for mod_name in (CG_MOD_ANTICLIP, CG_MOD_SMOOTH):
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
