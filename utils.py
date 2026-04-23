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
    garment_obj = getattr(settings, "garment_object", None)
    if garment_obj is None or not is_mesh_object(garment_obj):
        return

    enabled = bool(settings.enable_live_anti_clip)
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
