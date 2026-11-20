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

"""Modifiers Cloth Guard creates: body mask, Geometry Nodes anti-clip push-out, shape preserve."""

from __future__ import annotations

import bpy

from ..constants import (
    ANTICLIP_GN_MIN_VERSION,
    CG_MOD_ANTICLIP,
    CG_MOD_BODY_MASK,
    CG_MOD_SHAPE_PRESERVE,
    CG_MOD_SMOOTH,
    CG_PROP_ANTICLIP_STRENGTH_BASE,
    CG_SHAPEKEY_LIVE,
    CG_SHAPEKEY_LIVE_PRESERVE,
    CG_VG_BODY_MASK,
    CG_VG_CLIPPING,
    CG_VG_PINNED,
    CG_VG_PRESERVE_COLLAR,
    CG_VG_PRESERVE_HEM,
    CG_VG_PRESERVE_SEAMS,
    CORRECTION_PRESERVE_SCALE,
    GN_INPUTS_VIA_PROPERTIES,
)
from .body import _body_normal_sign
from .debug import log_debug
from .mesh_utils import is_mesh_object


def ensure_body_mask_modifier(body_obj: bpy.types.Object) -> bpy.types.MaskModifier:
    mod = body_obj.modifiers.get(CG_MOD_BODY_MASK)
    if mod is None:
        mod = body_obj.modifiers.new(name=CG_MOD_BODY_MASK, type="MASK")
    mod.vertex_group = CG_VG_BODY_MASK
    mod.invert_vertex_group = True
    # The mask must come after the Armature, so it hides vertices of the posed body.
    last_armature = max((i for i, m in enumerate(body_obj.modifiers) if m.type == "ARMATURE"), default=-1)
    index = body_obj.modifiers.find(mod.name)
    if index < last_armature:
        body_obj.modifiers.move(index, last_armature)
    return mod


def _ensure_group_socket(ng: bpy.types.NodeTree, in_out: str, socket_type: str, name: str) -> None:
    """Create a node-group input/output socket if missing (Blender 4.0 moved them to ng.interface)."""
    if bpy.app.version >= (4, 0, 0):
        for item in ng.interface.items_tree:
            if item.item_type == "SOCKET" and item.in_out == in_out and item.name == name:
                return
        ng.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
        return
    sockets = ng.inputs if in_out == "INPUT" else ng.outputs
    if sockets.find(name) < 0:
        sockets.new(socket_type, name)


def node_group_input_identifier(ng: bpy.types.NodeTree, name: str) -> str | None:
    """
    Identifier of a node-group input, which is how a Geometry Nodes modifier names its value.

    Never build it by hand ("Input_2" in 3.x, "Socket_2" in 4.x): ask the socket for its identifier.
    To read/write the modifier value use set_gn_modifier_input() / gn_modifier_input().
    """
    if bpy.app.version >= (4, 0, 0):
        for item in ng.interface.items_tree:
            if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == name:
                return item.identifier
        return None
    sock = ng.inputs.get(name)
    return sock.identifier if sock is not None else None


def gn_modifier_input(mod: bpy.types.Modifier, name: str) -> tuple | None:
    """
    (owner, property path) of a Geometry Nodes modifier input, so that on every Blender version:
    owner.keyframe_insert(path) keyframes it and owner.path_from_id(path) is its F-Curve data path.
    """
    key = node_group_input_identifier(mod.node_group, name)
    if key is None:
        return None
    if GN_INPUTS_VIA_PROPERTIES:
        return getattr(mod.properties.inputs, key), "value"
    return mod, f'["{key}"]'


def set_gn_modifier_input(mod: bpy.types.Modifier, name: str, value) -> None:
    key = node_group_input_identifier(mod.node_group, name)
    if key is None:
        return
    if GN_INPUTS_VIA_PROPERTIES:
        getattr(mod.properties.inputs, key).value = value
    else:
        mod[key] = value


def _enabled_socket(sockets, name: str):
    # Some nodes have one hidden socket per data type with the same name; use the enabled one.
    for s in sockets:
        if s.name == name and s.enabled:
            return s
    raise KeyError(f"Node socket '{name}' not found")


def anticlip_strength_base(garment_obj: bpy.types.Object, mod: bpy.types.Modifier, default: float) -> float:
    """The push distance (m) that Factor = 1.0 corresponds to."""
    if CG_PROP_ANTICLIP_STRENGTH_BASE in garment_obj:
        return float(garment_obj[CG_PROP_ANTICLIP_STRENGTH_BASE])
    # Files made by older add-on versions stored it on the Geometry Nodes modifier.
    if mod is not None and mod.type == "NODES" and not GN_INPUTS_VIA_PROPERTIES:
        return float(mod.get("cg_strength_base", default))
    return float(default)


def ensure_anticlip_modifiers(
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    *,
    offset_distance: float,
    smooth_iterations: int,
    smooth_strength: float,
    mode: str = "SAFE_PUSHOUT",
    strength: float = 1.0,
    max_push_distance: float = 0.03,
) -> tuple[bpy.types.Modifier, bpy.types.Modifier | None]:
    """
    Ensure non-destructive anti-clip helper modifiers for topology-changing garments.

    Important: this must not shrink/collapse the garment silhouette globally. For that reason,
    the default mode uses a Geometry Nodes push-out (nearest-body direction) restricted by CG_Clipping.
    """
    offset_distance = max(0.0, float(offset_distance))
    strength = max(0.0, min(1.0, float(strength)))
    max_push_distance = max(0.0, float(max_push_distance))

    def _ensure_anticlip_node_group() -> bpy.types.NodeTree:
        name = "CG_AntiClipPushOut"
        ng = bpy.data.node_groups.get(name)
        if ng is None:
            ng = bpy.data.node_groups.new(name=name, type="GeometryNodeTree")

        # Ensure Group IO sockets exist (keep stable names for animation/keyframing).
        _ensure_group_socket(ng, "INPUT", "NodeSocketGeometry", "Geometry")
        _ensure_group_socket(ng, "INPUT", "NodeSocketObject", "Body")
        _ensure_group_socket(ng, "INPUT", "NodeSocketFloat", "Offset")
        _ensure_group_socket(ng, "INPUT", "NodeSocketFloat", "Strength")
        _ensure_group_socket(ng, "INPUT", "NodeSocketFloat", "Max Push")
        _ensure_group_socket(ng, "INPUT", "NodeSocketFloat", "Factor")
        _ensure_group_socket(ng, "INPUT", "NodeSocketFloat", "Normal Sign")
        _ensure_group_socket(ng, "OUTPUT", "NodeSocketGeometry", "Geometry")
        if bpy.app.version >= (4, 0, 0):
            ng.is_modifier = True

        nodes = ng.nodes
        links = ng.links
        nodes.clear()

        n_in = nodes.new("NodeGroupInput")
        n_in.location = (-1000, 0)
        n_out = nodes.new("NodeGroupOutput")
        n_out.location = (900, 0)

        n_obj = nodes.new("GeometryNodeObjectInfo")
        n_obj.location = (-800, 300)
        n_obj.transform_space = "RELATIVE"

        # Nearest point on the body surface.
        n_prox = nodes.new("GeometryNodeProximity")
        n_prox.location = (-550, 350)
        n_prox.target_element = "FACES"

        # Body normal at that nearest point.
        n_normal = nodes.new("GeometryNodeInputNormal")
        n_normal.location = (-800, 100)
        n_sample = nodes.new("GeometryNodeSampleNearestSurface")
        n_sample.location = (-550, 150)
        n_sample.data_type = "FLOAT_VECTOR"

        n_pos = nodes.new("GeometryNodeInputPosition")
        n_pos.location = (-800, -50)

        # outward = normalize(body normal * Normal Sign)   (Normal Sign = -1 for an inside-out body)
        n_flip = nodes.new("ShaderNodeVectorMath")
        n_flip.location = (-350, 150)
        n_flip.operation = "SCALE"

        n_outward = nodes.new("ShaderNodeVectorMath")
        n_outward.location = (-150, 150)
        n_outward.operation = "NORMALIZE"

        # signed = (position - nearest) . outward   (+ outside the body, - inside)
        n_sub = nodes.new("ShaderNodeVectorMath")
        n_sub.location = (-350, 300)
        n_sub.operation = "SUBTRACT"

        n_dot = nodes.new("ShaderNodeVectorMath")
        n_dot.location = (-150, 300)
        n_dot.operation = "DOT_PRODUCT"

        # push = max(0, Offset - signed): a vertex 2 cm inside with a 1 cm offset is pushed 3 cm out.
        n_off_minus = nodes.new("ShaderNodeMath")
        n_off_minus.location = (-150, -150)
        n_off_minus.operation = "SUBTRACT"

        n_clamp0 = nodes.new("ShaderNodeMath")
        n_clamp0.location = (0, -150)
        n_clamp0.operation = "MAXIMUM"
        n_clamp0.inputs[1].default_value = 0.0

        n_mul_strength = nodes.new("ShaderNodeMath")
        n_mul_strength.location = (150, -150)
        n_mul_strength.operation = "MULTIPLY"

        n_mul_factor = nodes.new("ShaderNodeMath")
        n_mul_factor.location = (300, -150)
        n_mul_factor.operation = "MULTIPLY"

        n_min_maxpush = nodes.new("ShaderNodeMath")
        n_min_maxpush.location = (450, -150)
        n_min_maxpush.operation = "MINIMUM"

        n_mul_mag = nodes.new("ShaderNodeMath")
        n_mul_mag.location = (600, -150)
        n_mul_mag.operation = "MULTIPLY"

        n_attr_clip = nodes.new("GeometryNodeInputNamedAttribute")
        n_attr_clip.location = (-350, -350)
        n_attr_clip.data_type = "FLOAT"
        n_attr_clip.inputs["Name"].default_value = CG_VG_CLIPPING

        n_attr_pinned = nodes.new("GeometryNodeInputNamedAttribute")
        n_attr_pinned.location = (-350, -450)
        n_attr_pinned.data_type = "FLOAT"
        n_attr_pinned.inputs["Name"].default_value = CG_VG_PINNED

        n_attr_col = nodes.new("GeometryNodeInputNamedAttribute")
        n_attr_col.location = (-350, -550)
        n_attr_col.data_type = "FLOAT"
        n_attr_col.inputs["Name"].default_value = CG_VG_PRESERVE_COLLAR

        n_attr_hem = nodes.new("GeometryNodeInputNamedAttribute")
        n_attr_hem.location = (-350, -650)
        n_attr_hem.data_type = "FLOAT"
        n_attr_hem.inputs["Name"].default_value = CG_VG_PRESERVE_HEM

        n_attr_seam = nodes.new("GeometryNodeInputNamedAttribute")
        n_attr_seam.location = (-350, -750)
        n_attr_seam.data_type = "FLOAT"
        n_attr_seam.inputs["Name"].default_value = CG_VG_PRESERVE_SEAMS

        n_max_pres1 = nodes.new("ShaderNodeMath")
        n_max_pres1.location = (-150, -600)
        n_max_pres1.operation = "MAXIMUM"

        n_max_pres2 = nodes.new("ShaderNodeMath")
        n_max_pres2.location = (0, -650)
        n_max_pres2.operation = "MAXIMUM"

        n_one_minus_pin = nodes.new("ShaderNodeMath")
        n_one_minus_pin.location = (-150, -450)
        n_one_minus_pin.operation = "SUBTRACT"
        n_one_minus_pin.inputs[0].default_value = 1.0

        n_pres_scale = nodes.new("ShaderNodeMath")
        n_pres_scale.location = (150, -650)
        n_pres_scale.operation = "MULTIPLY"
        n_pres_scale.inputs[1].default_value = CORRECTION_PRESERVE_SCALE

        n_one_minus_pres = nodes.new("ShaderNodeMath")
        n_one_minus_pres.location = (300, -650)
        n_one_minus_pres.operation = "SUBTRACT"
        n_one_minus_pres.inputs[0].default_value = 1.0

        n_mul_scale1 = nodes.new("ShaderNodeMath")
        n_mul_scale1.location = (450, -450)
        n_mul_scale1.operation = "MULTIPLY"

        n_mul_scale2 = nodes.new("ShaderNodeMath")
        n_mul_scale2.location = (600, -450)
        n_mul_scale2.operation = "MULTIPLY"

        n_set = nodes.new("GeometryNodeSetPosition")
        n_set.location = (700, 50)

        n_scale_vec = nodes.new("ShaderNodeVectorMath")
        n_scale_vec.location = (550, 50)
        n_scale_vec.operation = "SCALE"

        # Wiring
        links.new(n_in.outputs["Body"], n_obj.inputs["Object"])
        # Input 0 is the target geometry on every version ("Target" in 3.x, "Geometry" in 4.x).
        links.new(n_obj.outputs["Geometry"], n_prox.inputs[0])
        links.new(n_obj.outputs["Geometry"], n_sample.inputs["Mesh"])
        links.new(n_normal.outputs["Normal"], _enabled_socket(n_sample.inputs, "Value"))
        links.new(n_in.outputs["Geometry"], n_set.inputs["Geometry"])

        links.new(_enabled_socket(n_sample.outputs, "Value"), n_flip.inputs[0])
        links.new(n_in.outputs["Normal Sign"], n_flip.inputs["Scale"])
        links.new(n_flip.outputs["Vector"], n_outward.inputs[0])

        links.new(n_pos.outputs["Position"], n_sub.inputs[0])
        links.new(n_prox.outputs["Position"], n_sub.inputs[1])
        links.new(n_sub.outputs["Vector"], n_dot.inputs[0])
        links.new(n_outward.outputs["Vector"], n_dot.inputs[1])

        links.new(n_in.outputs["Offset"], n_off_minus.inputs[0])
        links.new(n_dot.outputs["Value"], n_off_minus.inputs[1])
        links.new(n_off_minus.outputs["Value"], n_clamp0.inputs[0])
        links.new(n_clamp0.outputs["Value"], n_mul_strength.inputs[0])
        links.new(n_in.outputs["Strength"], n_mul_strength.inputs[1])
        links.new(n_mul_strength.outputs["Value"], n_mul_factor.inputs[0])
        links.new(n_in.outputs["Factor"], n_mul_factor.inputs[1])
        links.new(n_mul_factor.outputs["Value"], n_min_maxpush.inputs[0])
        links.new(n_in.outputs["Max Push"], n_min_maxpush.inputs[1])

        links.new(n_attr_col.outputs["Attribute"], n_max_pres1.inputs[0])
        links.new(n_attr_hem.outputs["Attribute"], n_max_pres1.inputs[1])
        links.new(n_max_pres1.outputs["Value"], n_max_pres2.inputs[0])
        links.new(n_attr_seam.outputs["Attribute"], n_max_pres2.inputs[1])
        links.new(n_max_pres2.outputs["Value"], n_pres_scale.inputs[0])
        links.new(n_pres_scale.outputs["Value"], n_one_minus_pres.inputs[1])

        links.new(n_attr_pinned.outputs["Attribute"], n_one_minus_pin.inputs[1])
        links.new(n_one_minus_pin.outputs["Value"], n_mul_scale1.inputs[0])
        links.new(n_one_minus_pres.outputs["Value"], n_mul_scale1.inputs[1])
        links.new(n_mul_scale1.outputs["Value"], n_mul_scale2.inputs[0])
        links.new(n_attr_clip.outputs["Attribute"], n_mul_scale2.inputs[1])

        # Combine the outward direction with magnitude and scale.
        links.new(n_outward.outputs["Vector"], n_scale_vec.inputs[0])
        links.new(n_min_maxpush.outputs["Value"], n_mul_mag.inputs[0])
        links.new(n_mul_scale2.outputs["Value"], n_mul_mag.inputs[1])
        links.new(n_mul_mag.outputs["Value"], n_scale_vec.inputs["Scale"])
        links.new(n_scale_vec.outputs["Vector"], n_set.inputs["Offset"])

        links.new(n_set.outputs["Geometry"], n_out.inputs["Geometry"])
        return ng

    def _rename_legacy(mod) -> None:
        # Preserve an incompatible existing modifier without deleting user data.
        mod.name = f"{CG_MOD_ANTICLIP}_Legacy"
        mod.show_viewport = False
        mod.show_render = False

    mod_ac = garment_obj.modifiers.get(CG_MOD_ANTICLIP)
    if mod_ac is not None and mod_ac.type not in {"NODES", "DISPLACE"}:
        _rename_legacy(mod_ac)
        mod_ac = None

    if bpy.app.version >= ANTICLIP_GN_MIN_VERSION:
        # Geometry Nodes push-out along the body's outward normal (not the garment normals).
        ng = _ensure_anticlip_node_group()
        if mod_ac is not None and mod_ac.type != "NODES":
            _rename_legacy(mod_ac)
            mod_ac = None
        if mod_ac is None:
            mod_ac = garment_obj.modifiers.new(name=CG_MOD_ANTICLIP, type="NODES")
        mod_ac.node_group = ng

        def _set_input(socket_name: str, value):
            set_gn_modifier_input(mod_ac, socket_name, value)

        body_mesh = body_obj.data
        body_mesh.calc_loop_triangles()
        normal_sign = _body_normal_sign(
            [v.co for v in body_mesh.vertices], [tuple(lt.vertices) for lt in body_mesh.loop_triangles]
        )

        _set_input("Body", body_obj)
        _set_input("Offset", float(offset_distance))
        _set_input("Strength", float(strength))
        _set_input("Max Push", float(max_push_distance if max_push_distance > 0.0 else offset_distance))
        _set_input("Factor", 1.0)
        _set_input("Normal Sign", float(normal_sign))
        garment_obj[CG_PROP_ANTICLIP_STRENGTH_BASE] = float(offset_distance)
    else:
        # Blender < 3.4 fallback: Displace (depends on garment normals; less robust, but topology-safe).
        if mod_ac is not None and mod_ac.type != "DISPLACE":
            _rename_legacy(mod_ac)
            mod_ac = None
        if mod_ac is None:
            mod_ac = garment_obj.modifiers.new(name=CG_MOD_ANTICLIP, type="DISPLACE")
        mod_ac.direction = "NORMAL"
        mod_ac.mid_level = 0.0
        mod_ac.vertex_group = CG_VG_CLIPPING
        garment_obj[CG_PROP_ANTICLIP_STRENGTH_BASE] = float(offset_distance)
        mod_ac.strength = float(offset_distance)

    mod_sm = garment_obj.modifiers.get(CG_MOD_SMOOTH)
    if str(mode) != "SAFE_PUSHOUT" and smooth_iterations > 0 and smooth_strength > 0.0:
        if mod_sm is None:
            mod_sm = garment_obj.modifiers.new(name=CG_MOD_SMOOTH, type="CORRECTIVE_SMOOTH")
        if hasattr(mod_sm, "vertex_group"):
            mod_sm.vertex_group = CG_VG_CLIPPING
        if hasattr(mod_sm, "factor"):
            mod_sm.factor = float(smooth_strength)
        if hasattr(mod_sm, "iterations"):
            mod_sm.iterations = int(smooth_iterations)
        # Smooth after the push-out.
        try:
            idx_sm = int(garment_obj.modifiers.find(mod_sm.name))
            if idx_sm >= 0:
                garment_obj.modifiers.move(idx_sm, len(garment_obj.modifiers) - 1)
        except RuntimeError as e:
            # Blender refuses the move (e.g. linked / library-override stack); smoothing still works,
            # just not at the very end of the stack.
            log_debug(bpy.context, "[Cloth Guard][AntiClip] could not move", mod_sm.name, "to the end:", e)
    else:
        if mod_sm is not None:
            # Don't delete user modifiers; CG_Smooth is add-on owned, so safe to remove.
            try:
                garment_obj.modifiers.remove(mod_sm)
            except RuntimeError as e:
                # Blender refuses to remove it (e.g. linked / library-override stack): switch it off instead.
                log_debug(bpy.context, "[Cloth Guard][AntiClip] could not remove", mod_sm.name, "- hiding it:", e)
                mod_sm.show_viewport = False
                mod_sm.show_render = False
            mod_sm = None

    return mod_ac, mod_sm


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
    if hasattr(mod, "rest_source"):
        try:
            mod.rest_source = "BIND"
        except TypeError as e:
            # "BIND" is not a valid rest source in this Blender version; the modifier keeps its default.
            log_debug(bpy.context, "[Cloth Guard][ShapePreserve] rest_source BIND not available:", e)
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
