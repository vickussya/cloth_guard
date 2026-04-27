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

from contextlib import contextmanager

import bpy
from bpy.types import Operator

from .utils import (
    CG_MOD_BODY_MASK,
    CG_MOD_ANTICLIP,
    CG_MOD_SMOOTH,
    CG_VG_BODY_MASK,
    CG_VG_CONTACT,
    CG_VG_CLIPPING,
    CG_SHAPEKEY_LIVE,
    CG_VG_SHAPE_DRIFT,
    CG_SHAPEKEY_REST,
    CG_SHAPEKEY_LIVE_PRESERVE,
    add_shapekey_driver_rotation_range,
    build_bvh,
    clear_vertex_group,
    correct_current_pose,
    detect_clipping,
    ensure_anticlip_modifiers,
    ensure_body_mask_modifier,
    ensure_live_correction_shapekey,
    ensure_live_preserve_shapekey,
    ensure_rest_shape_shapekey,
    ensure_vertex_group,
    is_mesh_object,
    store_rest_shape,
    analyze_shape_drift,
    generate_shape_preservation,
    write_weights_to_vertex_group,
)

# NOTE: Keep operator ids under the `cloth_guard.*` namespace (AGENTS.md).

_NON_DESTRUCTIVE_MSG = "Cloth Guard is non-destructive: it does not change topology or vertex order."

_TOPOLOGY_MODIFIER_TYPES = {
    # Common topology-changing modifiers
    "SUBSURF",
    "REMESH",
    "SKIN",
    "BOOLEAN",
    "DECIMATE",
    "MULTIRES",
    "SOLIDIFY",
    "SCREW",
    "ARRAY",
    "MIRROR",
    "NODES",  # Geometry Nodes can change topology
    "DYNAMIC_PAINT",
    "OCEAN",
    "PARTICLE_SYSTEM",
    "TRIANGULATE",
    "WELD",
    "BEVEL",
    "EDGE_SPLIT",
}


def _eval_vertex_count(obj: bpy.types.Object, depsgraph) -> int:
    obj_eval = obj.evaluated_get(depsgraph)
    me = obj_eval.to_mesh()
    try:
        return int(len(me.vertices))
    finally:
        obj_eval.to_mesh_clear()


def _base_vertex_count(obj: bpy.types.Object) -> int:
    me = getattr(obj, "data", None)
    return int(len(me.vertices)) if me is not None else 0


def _likely_topology_modifiers(obj: bpy.types.Object) -> list[bpy.types.Modifier]:
    mods = []
    for mod in obj.modifiers:
        if not getattr(mod, "show_viewport", True):
            continue
        if mod.type in _TOPOLOGY_MODIFIER_TYPES:
            mods.append(mod)
    return mods


@contextmanager
def _temporarily_disable_modifiers(obj: bpy.types.Object, mods: list[bpy.types.Modifier]):
    prev = []
    try:
        for mod in mods:
            prev.append((mod, bool(mod.show_viewport), bool(mod.show_render)))
            mod.show_viewport = False
            mod.show_render = False
        yield
    finally:
        for mod, sv, sr in prev:
            try:
                mod.show_viewport = sv
                mod.show_render = sr
            except Exception:
                pass


def _compatibility_report_line(
    *, garment_obj: bpy.types.Object, base_count: int, eval_count: int, mods: list[bpy.types.Modifier]
) -> str:
    status = "OK" if base_count == eval_count else "NOT OK"
    if mods:
        mod_txt = "; ".join([f"{m.name}({m.type})" for m in mods])
    else:
        mod_txt = "None detected"
    return f"{garment_obj.name}: base={base_count} eval={eval_count} => {status}; mods: {mod_txt}"


def _get_or_create_anticlip_mod(garment_obj: bpy.types.Object) -> bpy.types.Modifier | None:
    mod = garment_obj.modifiers.get(CG_MOD_ANTICLIP)
    if mod is None:
        return None
    return mod


def _keyframe_modifier_strength(*, garment_obj: bpy.types.Object, mod_name: str, frame: int, value: float) -> None:
    mod = garment_obj.modifiers.get(mod_name)
    if mod is None or not hasattr(mod, "strength"):
        return
    mod.strength = float(value)
    try:
        mod.keyframe_insert(data_path="strength", frame=int(frame))
    except Exception:
        return

    ad = garment_obj.animation_data
    if ad is None or ad.action is None:
        return
    data_path = f'modifiers["{mod.name}"].strength'
    fc = ad.action.fcurves.find(data_path=data_path)
    if fc is None:
        return
    for kp in fc.keyframe_points:
        if int(round(kp.co.x)) == int(frame):
            kp.interpolation = "CONSTANT"


def _run_detection_and_weights(
    *,
    context,
    settings,
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    depsgraph,
    offset_distance: float | None = None,
    detection_radius: float | None = None,
) -> tuple[object, int, int]:
    """
    Detect and write CG_Contact/CG_Clipping weights safely, using cage mode if enabled.
    Returns (detection_result, contact_affected, clipping_affected).
    """
    if offset_distance is None:
        offset_distance = float(settings.offset_distance)
    if detection_radius is None:
        detection_radius = float(settings.detection_radius)
    offset_distance = max(0.0, float(offset_distance))
    detection_radius = max(offset_distance, float(detection_radius))

    mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)
    # If topology differs, weight mapping to the base mesh is only reliable in cage mode,
    # so we force cage mode for detection regardless of the UI toggle.
    if mismatch:
        mods = _likely_topology_modifiers(garment_obj)
    else:
        mods = _likely_topology_modifiers(garment_obj) if settings.ignore_topology_modifiers else []

    with _temporarily_disable_modifiers(garment_obj, mods):
        if mods:
            context.view_layer.update()
            depsgraph = context.evaluated_depsgraph_get()
        det = detect_clipping(
            garment_obj=garment_obj,
            body_obj=body_obj,
            depsgraph=depsgraph,
            offset_distance=offset_distance,
            detection_radius=detection_radius,
            use_risk_area=settings.use_risk_area,
        )

    contact_affected = write_weights_to_vertex_group(garment_obj, CG_VG_CONTACT, det.contact_weights)
    clipping_affected = write_weights_to_vertex_group(garment_obj, CG_VG_CLIPPING, det.clipping_weights)
    return det, contact_affected, clipping_affected


def _topology_mismatch(*, garment_obj: bpy.types.Object, depsgraph) -> bool:
    return _base_vertex_count(garment_obj) != _eval_vertex_count(garment_obj, depsgraph)


def _run_shape_key_passes(
    *,
    context,
    settings,
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    depsgraph,
) -> tuple[object, object, int]:
    """
    Run multi-pass shape-key correction.

    Returns: (det_before, det_after, passes_used)
    """
    passes = int(getattr(settings, "correction_passes", 1))
    passes = max(1, min(5, passes))

    target_offset = max(0.0, float(settings.offset_distance + settings.safety_margin))
    det_before, contact_affected, clipping_affected = _run_detection_and_weights(
        context=context,
        settings=settings,
        garment_obj=garment_obj,
        body_obj=body_obj,
        depsgraph=depsgraph,
        offset_distance=target_offset,
        detection_radius=max(target_offset, settings.detection_radius),
    )

    # If nothing is clipping, we can skip heavy work.
    if int(det_before.stats.flagged_clipping) <= 0:
        return det_before, det_before, 0

    strength = float(settings.correction_strength) * float(settings.push_multiplier)
    strength = max(0.0, min(1.0, strength))

    for p in range(passes):
        stats = correct_current_pose(
            garment_obj=garment_obj,
            body_obj=body_obj,
            depsgraph=depsgraph,
            offset_distance=target_offset,
            detection_radius=max(target_offset, settings.detection_radius),
            correction_strength=strength,
            max_push_distance=settings.max_push_distance,
            smooth_iterations=settings.smooth_iterations,
            smooth_strength=settings.smooth_strength,
            use_risk_area=settings.use_risk_area,
            preserve_pinned_areas=settings.preserve_pinned_areas,
            accumulate=(p > 0),
        )

        # Re-evaluate to let the depsgraph see the updated shapekey.
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()

        det_after, _, _ = _run_detection_and_weights(
            context=context,
            settings=settings,
            garment_obj=garment_obj,
            body_obj=body_obj,
            depsgraph=depsgraph,
            offset_distance=target_offset,
            detection_radius=max(target_offset, settings.detection_radius),
        )

        print(
            "[Cloth Guard][Pass]",
            garment_obj.name,
            f"pass={p+1}/{passes}",
            f"before={int(det_before.stats.flagged_clipping)}",
            f"after={int(det_after.stats.flagged_clipping)}",
            f"changed={stats.changed_verts}",
            f"max_delta={stats.max_delta_distance:.6f} m",
        )

        if int(det_after.stats.flagged_clipping) <= 0:
            return det_before, det_after, p + 1

    return det_before, det_after, passes


class CG_OT_check_garment_compatibility(Operator):
    bl_idname = "cloth_guard.check_garment_compatibility"
    bl_label = "Check Garment Compatibility"
    bl_description = "Check whether garments can use shape-key correction, or will use helper/modifier mode (topology-changing stacks)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            bad = 0
            lines = []
            for garment_obj in garments:
                base_count = _base_vertex_count(garment_obj)
                eval_count = _eval_vertex_count(garment_obj, depsgraph)
                mods = _likely_topology_modifiers(garment_obj)
                if base_count != eval_count:
                    bad += 1
                line = _compatibility_report_line(
                    garment_obj=garment_obj,
                    base_count=base_count,
                    eval_count=eval_count,
                    mods=mods,
                )
                lines.append(line)
                print("[Cloth Guard][Compat]", line)

            if bad > 0:
                self.report(
                    {"WARNING"},
                    "Some garments have topology-changing modifiers (topology mismatch). "
                    "Cloth Guard will use a non-destructive helper/modifier workflow for those garments. "
                    "Enable 'Use Cage Mode For Topology-Changing Garments' for best results. See console for details. "
                    + _NON_DESTRUCTIVE_MSG,
                )
            else:
                self.report({"INFO"}, f"All garments OK for corrective shape keys ({len(garments)} checked). See console for details.")
        return {"FINISHED"}


def _settings(context):
    return context.scene.cg_settings


@contextmanager
def _temporary_mode_object(context):
    """
    Ensure Blender is in OBJECT mode for the duration of the context.

    This is required for operations like VertexGroup.add() and many data edits,
    which error when the relevant object is in Edit Mode.
    """
    view_layer = context.view_layer
    prev_active = view_layer.objects.active
    prev_mode = prev_active.mode if prev_active is not None else None

    # mode_set() operates on the active object; if no active object exists,
    # attempt to pick one (first selected) to allow mode change.
    if prev_active is None and context.selected_objects:
        view_layer.objects.active = context.selected_objects[0]
        prev_active = view_layer.objects.active
        prev_mode = prev_active.mode if prev_active is not None else None

    try:
        if prev_active is not None and prev_active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        yield
    finally:
        if prev_active is not None and prev_mode is not None and prev_mode != "OBJECT":
            try:
                view_layer.objects.active = prev_active
                bpy.ops.object.mode_set(mode=prev_mode)
            except Exception:
                pass


def _garments_from_list(settings):
    garments = []
    for item in settings.garments:
        obj = getattr(item, "object", None)
        if not getattr(item, "enabled", True):
            continue
        if obj is None or obj.type != "MESH":
            continue
        garments.append(obj)
    # Keep stable order based on list order.
    return garments


def _iter_garment_meshes_from_collection(coll):
    if coll is None:
        return []
    objs = []
    for obj in coll.all_objects:
        if obj is not None and obj.type == "MESH":
            objs.append(obj)
    objs.sort(key=lambda o: o.name.lower())
    return objs


def _validate_assigned_meshes(settings):
    body_obj = settings.body_object
    if body_obj is None or not is_mesh_object(body_obj):
        return None

    garments = _garments_from_list(settings)
    if not garments:
        # Backwards-compatible fallback: legacy collection/object fields.
        garments = _iter_garment_meshes_from_collection(getattr(settings, "garment_collection", None))
    if not garments and getattr(settings, "garment_object", None) is not None and is_mesh_object(settings.garment_object):
        garments = [settings.garment_object]

    if not garments:
        return None
    return body_obj, garments


def _shapekey_delta_stats(*, garment_obj: bpy.types.Object, shapekey_name: str) -> tuple[int, float]:
    if garment_obj.data.shape_keys is None:
        return 0, 0.0
    kb = garment_obj.data.shape_keys.key_blocks.get(shapekey_name)
    basis = garment_obj.data.shape_keys.key_blocks[0]
    if kb is None or len(kb.data) != len(basis.data):
        return 0, 0.0
    changed = 0
    max_delta = 0.0
    for i in range(len(kb.data)):
        d = kb.data[i].co - basis.data[i].co
        ln = float(d.length)
        if ln > 1e-12:
            changed += 1
            max_delta = max(max_delta, ln)
    return changed, max_delta


class CG_OT_add_selected_garments(Operator):
    bl_idname = "cloth_guard.add_selected_garments"
    bl_label = "Add Selected Garment"
    bl_description = "Add selected mesh objects to the garment list (ignores non-mesh; avoids duplicates)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        body_obj = settings.body_object

        selected_meshes = [o for o in context.selected_objects if o.type == "MESH"]
        if body_obj is not None:
            selected_meshes = [o for o in selected_meshes if o != body_obj]

        if not selected_meshes:
            self.report({"WARNING"}, "Select one or more garment mesh objects to add")
            return {"CANCELLED"}

        existing = {item.object for item in settings.garments if item.object is not None}
        added = 0
        for obj in selected_meshes:
            if obj in existing:
                continue
            item = settings.garments.add()
            item.object = obj
            item.enabled = True
            existing.add(obj)
            added += 1

        if added == 0:
            self.report({"INFO"}, "No new garments added (already in list)")
            return {"CANCELLED"}

        settings.active_garment_index = max(0, len(settings.garments) - 1)
        self.report({"INFO"}, f"Added {added} garment(s)")
        return {"FINISHED"}


class CG_OT_remove_active_garment(Operator):
    bl_idname = "cloth_guard.remove_active_garment"
    bl_label = "Remove Garment"
    bl_description = "Remove the active garment entry from the garment list"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        idx = int(settings.active_garment_index)
        if idx < 0 or idx >= len(settings.garments):
            self.report({"WARNING"}, "No garment entry selected")
            return {"CANCELLED"}
        settings.garments.remove(idx)
        settings.active_garment_index = max(0, min(idx, len(settings.garments) - 1))
        return {"FINISHED"}


class CG_OT_move_garment(Operator):
    bl_idname = "cloth_guard.move_garment"
    bl_label = "Move Garment"
    bl_description = "Move the active garment up/down in the list"
    bl_options = {"REGISTER", "UNDO"}

    direction: bpy.props.EnumProperty(
        items=(("UP", "Up", ""), ("DOWN", "Down", "")),
        default="UP",
    )

    def execute(self, context):
        settings = _settings(context)
        idx = int(settings.active_garment_index)
        if idx < 0 or idx >= len(settings.garments):
            return {"CANCELLED"}
        if self.direction == "UP":
            new_idx = idx - 1
        else:
            new_idx = idx + 1
        if new_idx < 0 or new_idx >= len(settings.garments):
            return {"CANCELLED"}
        settings.garments.move(idx, new_idx)
        settings.active_garment_index = new_idx
        return {"FINISHED"}


class CG_OT_setup(Operator):
    bl_idname = "cloth_guard.setup"
    bl_label = "Setup Cloth Guard"
    bl_description = "Validate objects and prepare Cloth Guard data structures (vertex groups/body mask/live correction)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "cg_settings", None)
        if settings is None:
            return False
        return settings.body_object is not None and (
            len(getattr(settings, "garments", [])) > 0 or settings.garment_object is not None or settings.garment_collection is not None
        )

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        with _temporary_mode_object(context):
            ensure_vertex_group(body_obj, CG_VG_BODY_MASK)
            for garment_obj in garments:
                ensure_vertex_group(garment_obj, CG_VG_CLIPPING)

                # Optional control groups (only create if missing; safe defaults).
                for name in (
                    "CG_RiskArea",
                    "CG_Pinned",
                    "CG_Preserve_Collar",
                    "CG_Preserve_Hem",
                    "CG_Preserve_Seams",
                ):
                    if garment_obj.vertex_groups.get(name) is None:
                        garment_obj.vertex_groups.new(name=name)

            ensure_body_mask_modifier(body_obj)
            for garment_obj in garments:
                ensure_live_correction_shapekey(garment_obj)
                if garment_obj.data.shape_keys is not None:
                    kb = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_LIVE)
                    if kb is not None:
                        kb.value = 1.0 if settings.enable_live_anti_clip else 0.0

        self.report({"INFO"}, f"Cloth Guard setup complete ({len(garments)} garment(s))")
        return {"FINISHED"}


class CG_OT_remove_setup(Operator):
    bl_idname = "cloth_guard.remove_setup"
    bl_label = "Remove Cloth Guard Setup"
    bl_description = "Remove Cloth Guard modifiers/groups created by the add-on (keeps optional control groups)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        with _temporary_mode_object(context):
            mod = body_obj.modifiers.get(CG_MOD_BODY_MASK)
            if mod is not None:
                body_obj.modifiers.remove(mod)
            vg = body_obj.vertex_groups.get(CG_VG_BODY_MASK)
            if vg is not None:
                body_obj.vertex_groups.remove(vg)

            for garment_obj in garments:
                vg = garment_obj.vertex_groups.get(CG_VG_CLIPPING)
                if vg is not None:
                    garment_obj.vertex_groups.remove(vg)

                if garment_obj.data.shape_keys is not None:
                    kb = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_LIVE)
                    if kb is not None:
                        garment_obj.shape_key_remove(kb)

        self.report({"INFO"}, "Cloth Guard setup removed")
        return {"FINISHED"}


class CG_OT_create_body_mask(Operator):
    bl_idname = "cloth_guard.create_body_mask"
    bl_label = "Create Body Mask"
    bl_description = "Hides body areas under the garment to remove small remaining intersections"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        # Vertex group writes require object mode.
        with _temporary_mode_object(context):
            return self._execute_object_mode(context, settings, body_obj, garments)

    def _execute_object_mode(self, context, settings, body_obj, garments):
        depsgraph = context.evaluated_depsgraph_get()
        body_eval = body_obj.evaluated_get(depsgraph)

        mask_dist = max(0.0, float(settings.mask_distance + settings.mask_expand))
        if mask_dist <= 0.0:
            self.report({"ERROR"}, "Mask distance must be > 0")
            return {"CANCELLED"}

        # Build a combined garment BVH in BODY LOCAL SPACE for stable queries.
        from mathutils.bvhtree import BVHTree

        body_mesh = body_eval.to_mesh()
        try:
            body_mesh.calc_loop_triangles()
            body_mw = body_eval.matrix_world
            body_mw_inv = body_mw.inverted_safe()

            combined_verts = []
            combined_tris = []
            vert_offset = 0

            for garment_obj in garments:
                garment_eval = garment_obj.evaluated_get(depsgraph)
                g_mesh = garment_eval.to_mesh()
                try:
                    g_mesh.calc_loop_triangles()
                    garment_to_body = body_mw_inv @ garment_eval.matrix_world

                    g_verts = [garment_to_body @ v.co for v in g_mesh.vertices]
                    g_tris = [tuple(vert_offset + vi for vi in lt.vertices) for lt in g_mesh.loop_triangles]

                    combined_verts.extend(g_verts)
                    combined_tris.extend(g_tris)
                    vert_offset += len(g_verts)
                finally:
                    garment_eval.to_mesh_clear()

            if not combined_verts or not combined_tris:
                self.report({"WARNING"}, "No garment triangles found in the selected collection")
                return {"CANCELLED"}

            bvh_garments = BVHTree.FromPolygons(combined_verts, combined_tris, all_triangles=True, epsilon=0.0)

            vg = ensure_vertex_group(body_obj, CG_VG_BODY_MASK)
            clear_vertex_group(body_obj, vg)

            b_mw = body_eval.matrix_world
            affected = 0
            for i, v in enumerate(body_mesh.vertices):
                world_co = b_mw @ v.co
                # Convert body vertex to body local for the combined BVH (it is already in body local, but keep explicit).
                body_local = body_mw_inv @ world_co
                nearest = bvh_garments.find_nearest(body_local)
                if nearest is None:
                    continue
                _, _, _, dist = nearest
                if dist is None:
                    continue
                if dist <= mask_dist:
                    vg.add([i], 1.0, "REPLACE")
                    affected += 1
        finally:
            body_eval.to_mesh_clear()

        ensure_body_mask_modifier(body_obj)
        self.report({"INFO"}, f"Body mask updated ({affected} vertices)")
        return {"FINISHED"}


class CG_OT_delete_body_mask(Operator):
    bl_idname = "cloth_guard.delete_body_mask"
    bl_label = "Delete Body Mask"
    bl_description = "Removes the Cloth Guard body mask and restores hidden body areas"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        body_obj = settings.body_object
        if body_obj is None or not is_mesh_object(body_obj):
            self.report({"ERROR"}, "Assign a valid Body mesh first")
            return {"CANCELLED"}

        with _temporary_mode_object(context):
            removed_mod = False
            removed_vg = False

            mod = body_obj.modifiers.get(CG_MOD_BODY_MASK)
            if mod is not None and getattr(mod, "type", None) == "MASK":
                body_obj.modifiers.remove(mod)
                removed_mod = True

            vg = body_obj.vertex_groups.get(CG_VG_BODY_MASK)
            if vg is not None:
                body_obj.vertex_groups.remove(vg)
                removed_vg = True

        if not removed_mod and not removed_vg:
            self.report({"INFO"}, "No Cloth Guard body mask found to delete")
            return {"CANCELLED"}

        parts = []
        if removed_vg:
            parts.append("vertex group")
        if removed_mod:
            parts.append("Mask modifier")
        self.report({"INFO"}, f"Deleted body mask ({' + '.join(parts)})")
        return {"FINISHED"}


class CG_OT_store_rest_shape(Operator):
    bl_idname = "cloth_guard.store_rest_shape"
    bl_label = "Store Rest Shape"
    bl_description = "Stores the clean garment form used as reference for shape preservation"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            stored = 0
            for garment_obj in garments:
                try:
                    # Store rest using a topology-stable cage evaluation if needed.
                    mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)
                    mods = _likely_topology_modifiers(garment_obj) if mismatch else []
                    with _temporarily_disable_modifiers(garment_obj, mods):
                        if mods:
                            context.view_layer.update()
                            depsgraph = context.evaluated_depsgraph_get()
                        ensure_rest_shape_shapekey(garment_obj)
                        written = store_rest_shape(garment_obj=garment_obj, depsgraph=depsgraph)
                    stored += 1
                    self.report({"INFO"}, f"Stored rest shape for {garment_obj.name}: {written} vertices")
                    print("[Cloth Guard][Rest]", garment_obj.name, f"written={written}")
                except Exception as e:
                    self.report({"WARNING"}, f"{garment_obj.name}: failed to store rest shape: {e}")

            self.report({"INFO"}, f"Stored rest shape for {stored} garment(s) (non-destructive)")
        return {"FINISHED"}


class CG_OT_analyze_shape_drift(Operator):
    bl_idname = "cloth_guard.analyze_shape_drift"
    bl_label = "Analyze Shape Drift"
    bl_description = "Checks how much the garment shape has changed from the stored reference"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            analyzed = 0
            for garment_obj in garments:
                try:
                    mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)
                    mods = _likely_topology_modifiers(garment_obj) if mismatch else []
                    with _temporarily_disable_modifiers(garment_obj, mods):
                        if mods:
                            context.view_layer.update()
                            depsgraph = context.evaluated_depsgraph_get()
                        stats, weights = analyze_shape_drift(
                            garment_obj=garment_obj,
                            depsgraph=depsgraph,
                            drift_threshold=settings.drift_threshold,
                            protect_borders=settings.protect_borders,
                        )
                    write_weights_to_vertex_group(garment_obj, CG_VG_SHAPE_DRIFT, weights)
                    analyzed += 1
                    avg_txt = f"{stats.avg_flagged_drift:.6f} m" if stats.avg_flagged_drift is not None else "n/a"
                    self.report(
                        {"INFO"},
                        f"{garment_obj.name}: drift flagged {stats.flagged_verts}/{stats.checked_verts}; max {stats.max_drift_distance:.6f} m; avg {avg_txt}",
                    )
                    print(
                        "[Cloth Guard][Drift]",
                        garment_obj.name,
                        f"flagged={stats.flagged_verts}",
                        f"max={stats.max_drift_distance:.6f}",
                    )
                except Exception as e:
                    self.report({"WARNING"}, f"{garment_obj.name}: drift analysis failed: {e}")

            if analyzed == 0:
                self.report({"ERROR"}, "No garments analyzed. Store Rest Shape first.")
                return {"CANCELLED"}
        return {"FINISHED"}


class CG_OT_generate_shape_preservation_current(Operator):
    bl_idname = "cloth_guard.generate_shape_preservation_current"
    bl_label = "Preserve Shape (Current Frame)"
    bl_description = "Creates a non-destructive correction to reduce unwanted deformation on the current frame"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            done = 0
            missing_rest = 0
            zero_delta = 0
            failed = 0
            for garment_obj in garments:
                try:
                    keys = garment_obj.data.shape_keys
                    if keys is None or keys.key_blocks.get(CG_SHAPEKEY_REST) is None:
                        missing_rest += 1
                        self.report({"WARNING"}, f"{garment_obj.name}: Rest Shape is missing. Click Store Rest Shape on a clean frame.")
                        continue

                    mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)
                    mods = _likely_topology_modifiers(garment_obj) if mismatch else []
                    with _temporarily_disable_modifiers(garment_obj, mods):
                        if mods:
                            context.view_layer.update()
                            depsgraph = context.evaluated_depsgraph_get()
                        ensure_live_preserve_shapekey(garment_obj)
                        changed, max_d = generate_shape_preservation(
                            garment_obj=garment_obj,
                            depsgraph=depsgraph,
                            strength=settings.shape_strength,
                            smoothing_iterations=settings.wrinkle_smoothing_iterations,
                            smoothing_strength=settings.wrinkle_smoothing_strength,
                            volume_preservation=settings.volume_preservation,
                            silhouette_preservation=settings.silhouette_preservation,
                            drift_threshold=settings.drift_threshold,
                            protect_borders=settings.protect_borders,
                            protect_groups=settings.protect_preserve_groups,
                        )
                    if changed <= 0:
                        zero_delta += 1
                        self.report(
                            {"INFO"},
                            f"{garment_obj.name}: no shape preservation change generated. "
                            "Try increasing Shape Strength or Wrinkle Smooth Strength.",
                        )
                        continue
                    done += 1
                    self.report({"INFO"}, f"{garment_obj.name}: preserve changed {changed} verts; max delta {max_d:.6f} m")
                    print("[Cloth Guard][Preserve]", garment_obj.name, f"changed={changed}", f"max_delta={max_d:.6f}")
                except Exception as e:
                    failed += 1
                    self.report({"WARNING"}, f"{garment_obj.name}: shape preservation failed: {e}")

            if done == 0:
                if missing_rest == len(garments):
                    self.report({"ERROR"}, "Rest Shape is missing for all garments. Click Store Rest Shape on a clean frame first.")
                elif missing_rest > 0:
                    self.report({"ERROR"}, f"Rest Shape is missing for {missing_rest} garment(s). See warnings above.")
                elif zero_delta > 0 and failed == 0:
                    self.report({"ERROR"}, "No preservation change was generated. Try stronger Shape Strength / Wrinkle Smooth Strength.")
                else:
                    self.report({"ERROR"}, "No preservation corrections generated. Check the console for details.")
                return {"CANCELLED"}

            settings.enable_live_anti_clip = True
        return {"FINISHED"}


class CG_OT_generate_shape_preservation_flagged(Operator):
    bl_idname = "cloth_guard.generate_shape_preservation_flagged"
    bl_label = "Preserve Shape (All Flagged Frames)"
    bl_description = "Batch: creates non-destructive shape preservation corrections for all flagged frames"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        if len(settings.problem_frames) == 0:
            self.report({"ERROR"}, "No problem frames stored. Run Scan Animation first.")
            return {"CANCELLED"}

        scene = context.scene
        prev_frame = int(scene.frame_current)
        frames = sorted(set(int(it.frame) for it in settings.problem_frames))

        view_layer = context.view_layer
        prev_active = view_layer.objects.active

        baked = 0
        try:
            with _temporary_mode_object(context):
                for frame in frames:
                    scene.frame_set(frame)
                    depsgraph = context.evaluated_depsgraph_get()
                    for garment_obj in garments:
                        try:
                            mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)
                            mods = _likely_topology_modifiers(garment_obj) if mismatch else []
                            with _temporarily_disable_modifiers(garment_obj, mods):
                                if mods:
                                    context.view_layer.update()
                                    depsgraph = context.evaluated_depsgraph_get()
                                ensure_live_preserve_shapekey(garment_obj)
                                changed, max_d = generate_shape_preservation(
                                    garment_obj=garment_obj,
                                    depsgraph=depsgraph,
                                    strength=settings.shape_strength,
                                    smoothing_iterations=settings.wrinkle_smoothing_iterations,
                                    smoothing_strength=settings.wrinkle_smoothing_strength,
                                    volume_preservation=settings.volume_preservation,
                                    silhouette_preservation=settings.silhouette_preservation,
                                    drift_threshold=settings.drift_threshold,
                                    protect_borders=settings.protect_borders,
                                    protect_groups=settings.protect_preserve_groups,
                                )
                            if changed <= 0:
                                continue

                            # Bake a per-frame preserve shape key (non-destructive; base topology).
                            view_layer.objects.active = garment_obj
                            key_name = f"CG_Preserve_{frame:04d}"
                            # Bake from mix with preserve live enabled.
                            preserve_kb = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_LIVE_PRESERVE)
                            if preserve_kb is not None:
                                preserve_kb.value = 1.0
                            tmp = garment_obj.shape_key_add(name="CG__TMP_PRESERVE_BAKE", from_mix=True)
                            try:
                                target = garment_obj.data.shape_keys.key_blocks.get(key_name)
                                if target is None:
                                    tmp.name = key_name
                                else:
                                    for i in range(len(tmp.data)):
                                        target.data[i].co = tmp.data[i].co
                            finally:
                                kb = garment_obj.data.shape_keys.key_blocks.get("CG__TMP_PRESERVE_BAKE")
                                if kb is not None:
                                    garment_obj.shape_key_remove(key_block=kb)

                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame - 1, value=0.0)
                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame, value=1.0)
                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame + 1, value=0.0)
                            baked += 1
                            print("[Cloth Guard][PreserveBake]", garment_obj.name, f"frame={frame}", f"key={key_name}", f"max_delta={max_d:.6f}")
                        except Exception as e:
                            self.report({"WARNING"}, f"{garment_obj.name} @ {frame}: preserve bake failed: {e}")
                settings.enable_live_anti_clip = True
        finally:
            scene.frame_set(prev_frame)
            view_layer.objects.active = prev_active

        self.report({"INFO"}, f"Preserve bake complete: {len(frames)} frame(s), {baked} baked item(s)")
        return {"FINISHED"}


class CG_OT_detect_clipping(Operator):
    bl_idname = "cloth_guard.detect_clipping"
    bl_label = "Detect Clipping"
    bl_description = "Finds garment vertices that are likely intersecting or too close to the body"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            total_checked = 0
            total_candidates = 0
            total_contact = 0
            total_clipping = 0
            global_min = None

            for garment_obj in garments:
                try:
                    res, contact_affected, clipping_affected = _run_detection_and_weights(
                        context=context,
                        settings=settings,
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        offset_distance=max(0.0, float(settings.offset_distance + settings.safety_margin)),
                        detection_radius=max(float(settings.offset_distance + settings.safety_margin), settings.detection_radius),
                    )
                except Exception as e:
                    self.report({"WARNING"}, f"{garment_obj.name}: {e}")
                    continue

                s = res.stats
                total_checked += s.checked_verts
                total_candidates += s.candidates_within_radius
                total_contact += contact_affected
                total_clipping += clipping_affected
                if s.min_nearest_distance is not None:
                    global_min = (
                        s.min_nearest_distance
                        if global_min is None
                        else min(global_min, s.min_nearest_distance)
                    )

                min_txt = f"{s.min_nearest_distance:.6f} m" if s.min_nearest_distance is not None else "n/a"
                print(
                    "[Cloth Guard][Detect]",
                    garment_obj.name,
                    f"checked={s.checked_verts}",
                    f"candidates={s.candidates_within_radius}",
                    f"contact={contact_affected}",
                    f"clipping={clipping_affected}",
                    f"min={min_txt}",
                )

        global_min_txt = f"{global_min:.6f} m" if global_min is not None else "n/a"
        msg = (
            f"Processed {len(garments)} garment(s); checked {total_checked} verts; "
            f"{total_candidates} within radius; {total_contact} contact; {total_clipping} clipping; min distance {global_min_txt}"
        )
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class CG_OT_select_clipping_vertices(Operator):
    bl_idname = "cloth_guard.select_clipping_vertices"
    bl_label = "Select Clipping Vertices"
    bl_description = "Select vertices flagged in the CG_Clipping vertex group (Edit Mode)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        garment_obj = context.view_layer.objects.active
        if garment_obj is None or garment_obj.type != "MESH" or garment_obj not in garments:
            self.report({"ERROR"}, "Make a garment mesh from the Garments list the active object, then run Select")
            return {"CANCELLED"}

        vg = garment_obj.vertex_groups.get(CG_VG_CLIPPING)
        if vg is None:
            self.report({"ERROR"}, f"Missing vertex group: {CG_VG_CLIPPING}")
            return {"CANCELLED"}

        view_layer = context.view_layer
        prev_active = view_layer.objects.active
        view_layer.objects.active = garment_obj
        garment_obj.select_set(True)
        try:
            if garment_obj.mode != "EDIT":
                bpy.ops.object.mode_set(mode="EDIT")

            import bmesh

            bm = bmesh.from_edit_mesh(garment_obj.data)
            mesh = garment_obj.data
            group_index = vg.index
            for v in bm.verts:
                v.select_set(False)
                for g in mesh.vertices[v.index].groups:
                    if g.group == group_index and g.weight > 0.0:
                        v.select_set(True)
                        break
            bmesh.update_edit_mesh(garment_obj.data, loop_triangles=False, destructive=False)
        finally:
            view_layer.objects.active = prev_active

        self.report({"INFO"}, f"Selected {CG_VG_CLIPPING} vertices")
        return {"FINISHED"}


def _update_correction_weights(context, *, report_to: Operator | None = None) -> int:
    return 0


class CG_OT_correct_current_pose(Operator):
    bl_idname = "cloth_guard.correct_current_pose"
    bl_label = "Update Live Corrective"
    bl_description = "Non-destructive: update the CG_LiveCorrection shape key for the current pose (Basis is never modified)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()

            total_checked = 0
            total_candidates = 0
            total_contact = 0
            total_clipping = 0
            total_corrected = 0
            global_min = None

            for garment_obj in garments:
                try:
                    target_offset = max(0.0, float(settings.offset_distance + settings.safety_margin))
                    det, contact_affected, clipping_affected = _run_detection_and_weights(
                        context=context,
                        settings=settings,
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        offset_distance=target_offset,
                        detection_radius=max(target_offset, settings.detection_radius),
                    )
                except Exception as e:
                    self.report({"WARNING"}, f"{garment_obj.name}: {e}")
                    continue

                # Mode selection:
                # - Topology stable: write CG_LiveCorrection (shape key)
                # - Topology changing: set up helper modifiers and refresh weights (non-destructive)
                is_mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)

                before_clipping = int(det.stats.flagged_clipping)

                if is_mismatch:
                    try:
                        ensure_anticlip_modifiers(
                            garment_obj=garment_obj,
                            body_obj=body_obj,
                            offset_distance=target_offset,
                            smooth_iterations=settings.smooth_iterations,
                            smooth_strength=settings.smooth_strength,
                        )
                        mod = garment_obj.modifiers.get(CG_MOD_ANTICLIP)
                        if mod is not None and hasattr(mod, "strength"):
                            mod.strength = 1.0
                        total_corrected += int(clipping_affected)
                        s = det.stats
                        total_checked += s.checked_verts
                        total_candidates += s.candidates_within_radius
                        total_contact += contact_affected
                        total_clipping += clipping_affected
                        if s.min_nearest_distance is not None:
                            global_min = s.min_nearest_distance if global_min is None else min(global_min, s.min_nearest_distance)
                        self.report(
                            {"INFO"},
                            f"{garment_obj.name}: topology-changing modifiers detected; using helper/modifier correction mode. {_NON_DESTRUCTIVE_MSG}",
                        )
                        # Residual check (still in cage mode so mapping stays stable).
                        context.view_layer.update()
                        depsgraph = context.evaluated_depsgraph_get()
                        det_after, _, _ = _run_detection_and_weights(
                            context=context,
                            settings=settings,
                            garment_obj=garment_obj,
                            body_obj=body_obj,
                            depsgraph=depsgraph,
                            offset_distance=target_offset,
                            detection_radius=max(target_offset, settings.detection_radius),
                        )
                        after_clipping = int(det_after.stats.flagged_clipping)
                        self.report(
                            {"INFO"},
                            f"{garment_obj.name}: {before_clipping} clipping before, {after_clipping} remaining (helper mode)",
                        )
                    except Exception as e:
                        self.report({"ERROR"}, f"{garment_obj.name}: helper/modifier correction failed: {e}")
                    continue

                try:
                    det_before, det_after, used = _run_shape_key_passes(
                        context=context,
                        settings=settings,
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                    )
                    after_clipping = int(det_after.stats.flagged_clipping)
                    self.report(
                        {"INFO"},
                        f"{garment_obj.name}: {before_clipping} clipping before, {after_clipping} remaining after {used} pass(es).",
                    )
                except Exception as e:
                    self.report({"ERROR"}, f"{garment_obj.name}: correction failed: {e}")
                    continue

                # Update global summary using before detection stats (stable) and after for min.
                s = det.stats
                total_checked += s.checked_verts
                total_candidates += s.candidates_within_radius
                total_contact += contact_affected
                total_clipping += clipping_affected
                total_corrected += int(before_clipping - after_clipping) if before_clipping >= after_clipping else 0
                if det_after.stats.min_nearest_distance is not None:
                    global_min = (
                        det_after.stats.min_nearest_distance
                        if global_min is None
                        else min(global_min, det_after.stats.min_nearest_distance)
                    )

            settings.enable_live_anti_clip = True
            global_min_txt = f"{global_min:.6f} m" if global_min is not None else "n/a"
            msg = (
                f"Processed {len(garments)} garment(s); checked {total_checked} verts; "
                f"{total_candidates} within radius; {total_contact} contact; {total_clipping} clipping; corrected {total_corrected}; "
                f"min distance {global_min_txt}"
            )
            print("[Cloth Guard][Correct]", msg)
            self.report({"INFO"}, msg)

        return {"FINISHED"}


class CG_OT_clear_live_correction(Operator):
    bl_idname = "cloth_guard.clear_live_correction"
    bl_label = "Clear Live Correction"
    bl_description = "Non-destructive: reset CG_LiveCorrection to match Basis (removes the live correction effect)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        with _temporary_mode_object(context):
            cleared = 0
            for garment_obj in garments:
                if garment_obj.type != "MESH":
                    continue
                try:
                    kb = ensure_live_correction_shapekey(garment_obj)
                    basis = garment_obj.data.shape_keys.key_blocks[0]
                    if len(kb.data) != len(basis.data):
                        raise RuntimeError("Shape key vertex count mismatch")
                    for i in range(len(basis.data)):
                        kb.data[i].co = basis.data[i].co
                    cleared += 1
                except Exception as e:
                    self.report({"WARNING"}, f"{garment_obj.name}: failed to clear live correction: {e}")

            self.report({"INFO"}, f"Cleared CG_LiveCorrection on {cleared} garment(s) (Basis unchanged)")
        return {"FINISHED"}


class CG_OT_scan_animation(Operator):
    bl_idname = "cloth_guard.scan_animation"
    bl_label = "Scan Animation"
    bl_description = "Scans the selected frame range and lists frames where clipping is detected"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        start = int(settings.scan_start_frame)
        end = int(settings.scan_end_frame)
        step = int(settings.scan_frame_step)
        if step <= 0:
            step = 1
        if end < start:
            start, end = end, start

        settings.problem_frames.clear()
        settings.active_problem_frame_index = 0

        scene = context.scene
        prev_frame = int(scene.frame_current)
        flagged_frames = 0

        try:
            for frame in range(start, end + 1, step):
                scene.frame_set(frame)
                depsgraph = context.evaluated_depsgraph_get()

                total_contact = 0
                total_clipping = 0
                min_dist = None
                details_parts: list[str] = []

                for garment_obj in garments:
                    try:
                        res, _, _ = _run_detection_and_weights(
                            context=context,
                            settings=settings,
                            garment_obj=garment_obj,
                            body_obj=body_obj,
                            depsgraph=depsgraph,
                            offset_distance=max(0.0, float(settings.offset_distance + settings.safety_margin)),
                            detection_radius=max(float(settings.offset_distance + settings.safety_margin), settings.detection_radius),
                        )
                    except Exception as e:
                        details_parts.append(f"{garment_obj.name}:ERR")
                        print("[Cloth Guard][Scan]", garment_obj.name, "ERROR", e)
                        continue

                    s = res.stats
                    total_contact += int(s.flagged_contact)
                    total_clipping += int(s.flagged_clipping)
                    if s.min_nearest_distance is not None:
                        min_dist = s.min_nearest_distance if min_dist is None else min(min_dist, s.min_nearest_distance)
                    if s.flagged_clipping > 0:
                        details_parts.append(f"{garment_obj.name}:{int(s.flagged_clipping)}")

                if total_clipping > 0:
                    item = settings.problem_frames.add()
                    item.frame = frame
                    item.contact_verts = int(total_contact)
                    item.clipping_verts = int(total_clipping)
                    item.min_distance = float(min_dist) if min_dist is not None else 0.0
                    item.details = ", ".join(details_parts)[:1024]
                    flagged_frames += 1

                if frame == start or frame == end or (frame - start) % max(step * 10, 1) == 0:
                    min_txt = f"{min_dist:.6f} m" if min_dist is not None else "n/a"
                    print(
                        "[Cloth Guard][Scan]",
                        f"frame={frame}",
                        f"contact={total_contact}",
                        f"clipping={total_clipping}",
                        f"min={min_txt}",
                    )
        finally:
            scene.frame_set(prev_frame)

        self.report({"INFO"}, f"Scan complete: {flagged_frames} problem frame(s) flagged ({start}-{end} step {step})")
        return {"FINISHED"}


class CG_OT_clear_problem_frames(Operator):
    bl_idname = "cloth_guard.clear_problem_frames"
    bl_label = "Clear Problem Frames"
    bl_description = "Clear the problem frames list"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        settings.problem_frames.clear()
        settings.active_problem_frame_index = 0
        self.report({"INFO"}, "Problem frames cleared")
        return {"FINISHED"}


class CG_OT_go_to_problem_frame(Operator):
    bl_idname = "cloth_guard.go_to_problem_frame"
    bl_label = "Go To Problem Frame"
    bl_description = "Jump the timeline to the selected problem frame"
    bl_options = {"REGISTER", "UNDO"}

    frame: bpy.props.IntProperty(name="Frame", default=-1, min=-1)

    def execute(self, context):
        settings = _settings(context)
        scene = context.scene

        target = int(self.frame)
        if target < 0:
            idx = int(settings.active_problem_frame_index)
            if idx < 0 or idx >= len(settings.problem_frames):
                self.report({"ERROR"}, "Select a problem frame first")
                return {"CANCELLED"}
            target = int(settings.problem_frames[idx].frame)

        scene.frame_set(target)
        self.report({"INFO"}, f"Set frame to {target}")
        return {"FINISHED"}


def _bake_live_correction_to_key(garment_obj: bpy.types.Object, name: str) -> None:
    """
    Bake the current mixed shape (including CG_LiveCorrection) into a named shape key.

    If a key with the same name exists, it is overwritten (data copied).
    """
    if garment_obj.type != "MESH":
        raise RuntimeError("Garment is not a mesh")

    ensure_live_correction_shapekey(garment_obj).value = 1.0
    if garment_obj.data.shape_keys is None:
        garment_obj.shape_key_add(name="Basis", from_mix=False)

    keys = garment_obj.data.shape_keys
    target = keys.key_blocks.get(name)

    tmp = garment_obj.shape_key_add(name="CG__TMP_BAKE", from_mix=True)
    try:
        if target is None:
            tmp.name = name
            return
        if len(tmp.data) != len(target.data):
            raise RuntimeError("Shape key vertex count mismatch")
        for i in range(len(tmp.data)):
            target.data[i].co = tmp.data[i].co
    finally:
        kb = keys.key_blocks.get("CG__TMP_BAKE")
        if kb is not None:
            garment_obj.shape_key_remove(key_block=kb)


def _keyframe_shapekey_value(*, garment_obj: bpy.types.Object, key_name: str, frame: int, value: float) -> None:
    if garment_obj.data.shape_keys is None:
        return
    kb = garment_obj.data.shape_keys.key_blocks.get(key_name)
    if kb is None:
        return
    kb.value = float(value)
    kb.keyframe_insert("value", frame=int(frame))
    ad = garment_obj.data.shape_keys.animation_data
    if ad is None or ad.action is None:
        return
    data_path = f'key_blocks["{kb.name}"].value'
    fc = ad.action.fcurves.find(data_path=data_path)
    if fc is None:
        return
    for kp in fc.keyframe_points:
        if int(round(kp.co.x)) == int(frame):
            kp.interpolation = "CONSTANT"


class CG_OT_generate_correction_current_frame(Operator):
    bl_idname = "cloth_guard.generate_correction_current_frame"
    bl_label = "Generate Correction For Current Frame"
    bl_description = "Creates a non-destructive anti-clipping correction for the current frame"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        frame = int(context.scene.frame_current)
        depsgraph = context.evaluated_depsgraph_get()

        view_layer = context.view_layer
        prev_active = view_layer.objects.active

        baked = 0
        with _temporary_mode_object(context):
            for garment_obj in garments:
                try:
                    target_offset = max(0.0, float(settings.offset_distance + settings.safety_margin))
                    det, _, clipping_affected = _run_detection_and_weights(
                        context=context,
                        settings=settings,
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        offset_distance=target_offset,
                        detection_radius=max(target_offset, settings.detection_radius),
                    )

                    # Topology mismatch: bake as helper/modifier strength keyframes instead of shape keys.
                    if _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph):
                        ensure_anticlip_modifiers(
                            garment_obj=garment_obj,
                            body_obj=body_obj,
                            offset_distance=target_offset,
                            smooth_iterations=settings.smooth_iterations,
                            smooth_strength=settings.smooth_strength,
                        )
                        _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame - 1, value=0.0)
                        _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame, value=1.0)
                        _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame + 1, value=0.0)
                        baked += 1
                        print(
                            "[Cloth Guard][BakeFrame][Helper]",
                            garment_obj.name,
                            f"frame={frame}",
                            f"clipping={int(det.stats.flagged_clipping)}",
                            f"clipping_vg={clipping_affected}",
                            f"mod={CG_MOD_ANTICLIP}",
                        )
                        continue

                    det_before, det_after, used = _run_shape_key_passes(
                        context=context,
                        settings=settings,
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                    )
                    changed_live, max_live = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=CG_SHAPEKEY_LIVE)
                    if changed_live <= 0 or max_live <= 1e-12:
                        self.report({"INFO"}, f"{garment_obj.name}: no correction delta generated at frame {frame}")
                        continue
                    view_layer.objects.active = garment_obj
                    key_name = f"CG_Frame_{frame:04d}"
                    _bake_live_correction_to_key(garment_obj, key_name)
                    _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame - 1, value=0.0)
                    _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame, value=1.0)
                    _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame + 1, value=0.0)
                    changed, max_d = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=key_name)
                    print(
                        "[Cloth Guard][BakeFrame]",
                        garment_obj.name,
                        f"frame={frame}",
                        f"before={int(det_before.stats.flagged_clipping)}",
                        f"after={int(det_after.stats.flagged_clipping)}",
                        f"passes={used}",
                        f"delta_verts={changed}",
                        f"max_delta={max_d:.6f}",
                        f"key={key_name}",
                    )
                    baked += 1
                except Exception as e:
                    self.report({"WARNING"}, f"{garment_obj.name}: bake failed: {e}")
            settings.enable_live_anti_clip = True

        view_layer.objects.active = prev_active
        self.report({"INFO"}, f"Baked per-frame correctives on {baked} garment(s) at frame {frame}")
        return {"FINISHED"}


class CG_OT_generate_corrections_flagged_frames(Operator):
    bl_idname = "cloth_guard.generate_corrections_flagged_frames"
    bl_label = "Generate Corrections For All Flagged Frames"
    bl_description = "Batch: creates non-destructive anti-clipping corrections for all flagged frames"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        if len(settings.problem_frames) == 0:
            self.report({"ERROR"}, "No problem frames stored. Run Scan Animation first.")
            return {"CANCELLED"}

        scene = context.scene
        prev_frame = int(scene.frame_current)

        view_layer = context.view_layer
        prev_active = view_layer.objects.active

        frames = [int(it.frame) for it in settings.problem_frames]
        frames = sorted(set(frames))

        baked_keys = 0
        try:
            with _temporary_mode_object(context):
                for frame in frames:
                    scene.frame_set(frame)
                    depsgraph = context.evaluated_depsgraph_get()
                    for garment_obj in garments:
                        try:
                            target_offset = max(0.0, float(settings.offset_distance + settings.safety_margin))
                            det, _, clipping_affected = _run_detection_and_weights(
                                context=context,
                                settings=settings,
                                garment_obj=garment_obj,
                                body_obj=body_obj,
                                depsgraph=depsgraph,
                                offset_distance=target_offset,
                                detection_radius=max(target_offset, settings.detection_radius),
                            )

                            if _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph):
                                ensure_anticlip_modifiers(
                                    garment_obj=garment_obj,
                                    body_obj=body_obj,
                                    offset_distance=target_offset,
                                    smooth_iterations=settings.smooth_iterations,
                                    smooth_strength=settings.smooth_strength,
                                )
                                _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame - 1, value=0.0)
                                _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame, value=1.0)
                                _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame + 1, value=0.0)
                                baked_keys += 1
                                print(
                                    "[Cloth Guard][BakeFlagged][Helper]",
                                    garment_obj.name,
                                    f"frame={frame}",
                                    f"clipping={int(det.stats.flagged_clipping)}",
                                    f"clipping_vg={clipping_affected}",
                                    f"mod={CG_MOD_ANTICLIP}",
                                )
                                continue

                            det_before, det_after, used = _run_shape_key_passes(
                                context=context,
                                settings=settings,
                                garment_obj=garment_obj,
                                body_obj=body_obj,
                                depsgraph=depsgraph,
                            )
                            changed_live, max_live = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=CG_SHAPEKEY_LIVE)
                            if changed_live <= 0 or max_live <= 1e-12:
                                print("[Cloth Guard][BakeFlagged]", garment_obj.name, f"frame={frame}", "NO_DELTA")
                                continue
                            view_layer.objects.active = garment_obj
                            key_name = f"CG_Frame_{frame:04d}"
                            _bake_live_correction_to_key(garment_obj, key_name)
                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame - 1, value=0.0)
                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame, value=1.0)
                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame + 1, value=0.0)
                            changed, max_d = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=key_name)
                            print(
                                "[Cloth Guard][BakeFlagged]",
                                garment_obj.name,
                                f"frame={frame}",
                                f"before={int(det_before.stats.flagged_clipping)}",
                                f"after={int(det_after.stats.flagged_clipping)}",
                                f"passes={used}",
                                f"delta_verts={changed}",
                                f"max_delta={max_d:.6f}",
                                f"key={key_name}",
                            )
                            baked_keys += 1
                        except Exception as e:
                            self.report({"WARNING"}, f"{garment_obj.name} @ {frame}: bake failed: {e}")
                settings.enable_live_anti_clip = True
        finally:
            scene.frame_set(prev_frame)
            view_layer.objects.active = prev_active

        self.report({"INFO"}, f"Generated per-frame correctives: {len(frames)} frame(s), {baked_keys} baked key(s)")
        return {"FINISHED"}


class CG_OT_refresh_live_correction(Operator):
    bl_idname = "cloth_guard.refresh_live_correction"
    bl_label = "Refresh Live Correction"
    bl_description = "Recompute proximity weights for the current pose/frame"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            total_corrected = 0
            global_min = None
            for garment_obj in garments:
                try:
                    stats = correct_current_pose(
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        offset_distance=settings.offset_distance,
                        detection_radius=max(settings.offset_distance, settings.detection_radius),
                        correction_strength=settings.correction_strength,
                        max_push_distance=settings.max_push_distance,
                        smooth_iterations=settings.smooth_iterations,
                        smooth_strength=settings.smooth_strength,
                        use_risk_area=settings.use_risk_area,
                        preserve_pinned_areas=settings.preserve_pinned_areas,
                    )
                except Exception as e:
                    self.report({"WARNING"}, f"{garment_obj.name}: {e}")
                    continue
                total_corrected += stats.corrected_verts
                if stats.min_nearest_distance is not None:
                    global_min = (
                        stats.min_nearest_distance
                        if global_min is None
                        else min(global_min, stats.min_nearest_distance)
                    )

            min_txt = f"{global_min:.6f} m" if global_min is not None else "n/a"
            self.report({"INFO"}, f"Refreshed live correction (corrected {total_corrected}; min distance {min_txt})")
        return {"FINISHED"}


class CG_OT_create_corrective_shapekey(Operator):
    bl_idname = "cloth_guard.create_corrective_shapekey"
    bl_label = "Create Corrective Shape Key From Current Pose"
    bl_description = "Bake the current corrected result into a new shape key (optionally driven by a bone rotation range)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        garment_obj = context.view_layer.objects.active
        if garment_obj is None or garment_obj.type != "MESH" or garment_obj not in garments:
            self.report({"ERROR"}, "Make a garment mesh from the Garments list the active object to bake a corrective")
            return {"CANCELLED"}

        depsgraph = context.evaluated_depsgraph_get()
        if _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph):
            self.report(
                {"INFO"},
                f"{garment_obj.name}: shape-key corrective is not available with topology-changing modifiers; "
                "use Generate Correction (Current) / Generate Corrections (All Flagged) which switches to helper/modifier mode.",
            )
            return {"CANCELLED"}

        # Ensure live correction exists and is enabled so baking includes it.
        with _temporary_mode_object(context):
            ensure_live_correction_shapekey(garment_obj)
            settings.enable_live_anti_clip = True
            depsgraph = context.evaluated_depsgraph_get()
            det_before, det_after, used = _run_shape_key_passes(
                context=context,
                settings=settings,
                garment_obj=garment_obj,
                body_obj=settings.body_object,
                depsgraph=depsgraph,
            )
            changed_live, max_live = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=CG_SHAPEKEY_LIVE)
            if changed_live <= 0 or max_live <= 1e-12:
                self.report({"WARNING"}, "No correction delta generated for this pose; corrective shape key may be empty")
            else:
                self.report(
                    {"INFO"},
                    f"Live corrective updated: {int(det_before.stats.flagged_clipping)} clipping before, "
                    f"{int(det_after.stats.flagged_clipping)} remaining after {used} pass(es).",
                )

        view_layer = context.view_layer
        prev_active = view_layer.objects.active
        view_layer.objects.active = garment_obj
        garment_obj.select_set(True)

        try:
            new_key = garment_obj.shape_key_add(
                name=(settings.corrective_name.strip() or "CG_Corrective"),
                from_mix=True,
            )
        except Exception as e:
            self.report({"ERROR"}, f"Failed to bake corrective shape key: {e}")
            return {"CANCELLED"}
        finally:
            view_layer.objects.active = prev_active

        new_key.value = 1.0
        changed, max_d = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=new_key.name)
        if changed <= 0 or max_d <= 1e-12:
            self.report({"WARNING"}, f"Corrective created but appears empty: {new_key.name}")
        else:
            self.report({"INFO"}, f"{new_key.name} changed {changed} verts; max delta {max_d:.6f} m")

        if settings.driver_enable:
            try:
                arm = settings.driver_armature
                bone = settings.driver_bone.strip()
                if arm is None or bone == "":
                    raise RuntimeError("Set Armature and Bone for driver linking")
                add_shapekey_driver_rotation_range(
                    garment_obj=garment_obj,
                    shapekey_name=new_key.name,
                    armature_obj=arm,
                    bone_name=bone,
                    axis=settings.driver_axis,
                    min_angle_rad=float(settings.driver_min_angle),
                    max_angle_rad=float(settings.driver_max_angle),
                )
            except Exception as e:
                self.report({"WARNING"}, f"Corrective created, but driver linking failed: {e}")

        print("[Cloth Guard][Corrective]", garment_obj.name, f"frame={context.scene.frame_current}", f"key={new_key.name}", f"delta_verts={changed}", f"max_delta={max_d:.6f}")
        return {"FINISHED"}


class CG_OT_bake_corrections(Operator):
    bl_idname = "cloth_guard.bake_corrections"
    bl_label = "Bake Corrections"
    bl_description = "Bake the current corrected pose into a shape key (MVP)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        frame = context.scene.frame_current
        if not settings.corrective_name or settings.corrective_name.strip() == "":
            settings.corrective_name = f"CG_Bake_{frame}"
        return bpy.ops.cloth_guard.create_corrective_shapekey()
