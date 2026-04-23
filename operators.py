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
    CG_VG_BODY_MASK,
    CG_VG_CONTACT,
    CG_VG_CLIPPING,
    CG_SHAPEKEY_LIVE,
    add_shapekey_driver_rotation_range,
    build_bvh,
    clear_vertex_group,
    correct_current_pose,
    detect_clipping,
    ensure_body_mask_modifier,
    ensure_live_correction_shapekey,
    ensure_vertex_group,
    is_mesh_object,
    write_weights_to_vertex_group,
)


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
    bl_description = "Create/update a Mask modifier on the body to hide regions under the garment"
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
            return self._execute_object_mode(context, body_obj, garments)

    def _execute_object_mode(self, context, body_obj, garments):
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


class CG_OT_detect_clipping(Operator):
    bl_idname = "cloth_guard.detect_clipping"
    bl_label = "Detect Clipping"
    bl_description = "Detect garment vertices too close to the body and store results in CG_Clipping"
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
                    res = detect_clipping(
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        offset_distance=settings.offset_distance,
                        detection_radius=max(settings.offset_distance, settings.detection_radius),
                        use_risk_area=settings.use_risk_area,
                    )
                except Exception as e:
                    self.report({"WARNING"}, f"{garment_obj.name}: {e}")
                    continue

                contact_affected = write_weights_to_vertex_group(garment_obj, CG_VG_CONTACT, res.contact_weights)
                clipping_affected = write_weights_to_vertex_group(garment_obj, CG_VG_CLIPPING, res.clipping_weights)

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
    bl_label = "Correct Current Pose"
    bl_description = "Detect proximity/penetration against the body and write a live corrective shape key for the current pose (non-simulation)"
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
                    det = detect_clipping(
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        offset_distance=settings.offset_distance,
                        detection_radius=max(settings.offset_distance, settings.detection_radius),
                        use_risk_area=settings.use_risk_area,
                    )
                except Exception as e:
                    self.report({"WARNING"}, f"{garment_obj.name}: {e}")
                    continue

                contact_affected = write_weights_to_vertex_group(garment_obj, CG_VG_CONTACT, det.contact_weights)
                clipping_affected = write_weights_to_vertex_group(garment_obj, CG_VG_CLIPPING, det.clipping_weights)

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
                    self.report({"WARNING"}, f"{garment_obj.name}: correction failed: {e}")
                    continue

                s = det.stats
                total_checked += s.checked_verts
                total_candidates += s.candidates_within_radius
                total_contact += contact_affected
                total_clipping += clipping_affected
                total_corrected += stats.corrected_verts
                if s.min_nearest_distance is not None:
                    global_min = (
                        s.min_nearest_distance
                        if global_min is None
                        else min(global_min, s.min_nearest_distance)
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

        # Ensure live correction exists and is enabled so baking includes it.
        with _temporary_mode_object(context):
            ensure_live_correction_shapekey(garment_obj)
            settings.enable_live_anti_clip = True

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

        self.report({"INFO"}, f"Corrective shape key created: {new_key.name}")
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
