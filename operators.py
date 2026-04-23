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

import bpy
from bpy.types import Operator

from .utils import (
    CG_MOD_BODY_MASK,
    CG_VG_BODY_MASK,
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


def _validate_assigned_meshes(settings):
    body_obj = settings.body_object
    garment_obj = settings.garment_object
    if body_obj is None or garment_obj is None:
        return None
    if not is_mesh_object(body_obj) or not is_mesh_object(garment_obj):
        return None
    return body_obj, garment_obj


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
        return settings.body_object is not None and settings.garment_object is not None

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign valid Body and Garment mesh objects first")
            return {"CANCELLED"}
        body_obj, garment_obj = validated

        ensure_vertex_group(body_obj, CG_VG_BODY_MASK)
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
        ensure_live_correction_shapekey(garment_obj)
        if garment_obj.data.shape_keys is not None:
            kb = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_LIVE)
            if kb is not None:
                kb.value = 1.0 if settings.enable_live_anti_clip else 0.0

        self.report({"INFO"}, "Cloth Guard setup complete")
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
            self.report({"ERROR"}, "Assign valid Body and Garment mesh objects first")
            return {"CANCELLED"}
        body_obj, garment_obj = validated

        mod = body_obj.modifiers.get(CG_MOD_BODY_MASK)
        if mod is not None:
            body_obj.modifiers.remove(mod)
        vg = body_obj.vertex_groups.get(CG_VG_BODY_MASK)
        if vg is not None:
            body_obj.vertex_groups.remove(vg)

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
            self.report({"ERROR"}, "Assign valid Body and Garment mesh objects first")
            return {"CANCELLED"}
        body_obj, garment_obj = validated

        depsgraph = context.evaluated_depsgraph_get()
        garment_eval = garment_obj.evaluated_get(depsgraph)
        body_eval = body_obj.evaluated_get(depsgraph)

        mask_dist = max(0.0, float(settings.mask_distance + settings.mask_expand))
        if mask_dist <= 0.0:
            self.report({"ERROR"}, "Mask distance must be > 0")
            return {"CANCELLED"}

        bvh_garment = build_bvh(garment_eval, depsgraph)

        vg = ensure_vertex_group(body_obj, CG_VG_BODY_MASK)
        clear_vertex_group(body_obj, vg)

        body_mesh = body_eval.to_mesh()
        try:
            b_mw = body_eval.matrix_world
            affected = 0
            for i, v in enumerate(body_mesh.vertices):
                world_co = b_mw @ v.co
                nearest = bvh_garment.find_nearest(world_co)
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
            self.report({"ERROR"}, "Assign valid Body and Garment mesh objects first")
            return {"CANCELLED"}
        body_obj, garment_obj = validated

        depsgraph = context.evaluated_depsgraph_get()
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
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        hard = [0.0] * len(garment_obj.data.vertices)
        for i in res.clipping_indices:
            hard[i] = 1.0
        affected = write_weights_to_vertex_group(garment_obj, CG_VG_CLIPPING, hard)

        s = res.stats
        min_d = s.min_nearest_distance
        avg_d = s.avg_flagged_distance
        min_txt = f"{min_d:.6f} m" if min_d is not None else "n/a"
        avg_txt = f"{avg_d:.6f} m" if avg_d is not None else "n/a"

        if affected > 0:
            msg = (
                f"Checked {s.checked_verts} garment verts; {s.candidates_within_radius} within radius; "
                f"{affected} flagged as clipping; min distance {min_txt}; avg flagged {avg_txt}"
            )
        else:
            if s.candidates_within_radius > 0:
                msg = (
                    f"No clipping under current threshold; checked {s.checked_verts} verts; "
                    f"{s.candidates_within_radius} within radius; min distance {min_txt}"
                )
            else:
                msg = f"Checked {s.checked_verts} garment verts; min distance {min_txt}"

        print("[Cloth Guard][Detect]", msg)
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
            self.report({"ERROR"}, "Assign valid Body and Garment mesh objects first")
            return {"CANCELLED"}
        _, garment_obj = validated

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
            self.report({"ERROR"}, "Assign valid Body and Garment mesh objects first")
            return {"CANCELLED"}
        body_obj, garment_obj = validated

        depsgraph = context.evaluated_depsgraph_get()

        # Update clipping group + gather debug stats.
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
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        hard = [0.0] * len(garment_obj.data.vertices)
        for i in det.clipping_indices:
            hard[i] = 1.0
        write_weights_to_vertex_group(garment_obj, CG_VG_CLIPPING, hard)

        # Apply correction based on evaluated geometry.
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
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        settings.enable_live_anti_clip = True

        s = det.stats
        min_d = s.min_nearest_distance
        min_txt = f"{min_d:.6f} m" if min_d is not None else "n/a"
        msg = (
            f"Checked {s.checked_verts} verts; {s.candidates_within_radius} within radius; "
            f"{s.flagged_clipping} flagged; corrected {stats.corrected_verts}; min distance {min_txt}"
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
            self.report({"ERROR"}, "Assign valid Body and Garment mesh objects first")
            return {"CANCELLED"}
        body_obj, garment_obj = validated

        depsgraph = context.evaluated_depsgraph_get()
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
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        min_d = stats.min_nearest_distance
        min_txt = f"{min_d:.6f} m" if min_d is not None else "n/a"
        self.report({"INFO"}, f"Refreshed live correction (corrected {stats.corrected_verts}; min distance {min_txt})")
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
            self.report({"ERROR"}, "Assign valid Body and Garment mesh objects first")
            return {"CANCELLED"}
        body_obj, garment_obj = validated

        # Ensure live correction exists and is enabled so baking includes it.
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
