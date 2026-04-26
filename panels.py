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

import bpy
from bpy.types import Panel
from bpy.types import UIList


class CG_UL_garments(UIList):
    bl_idname = "CG_UL_garments"

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname, index
    ):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        if item.object is None:
            row.label(text="<None>", icon="MESH_DATA")
        else:
            row.prop(item, "object", text="", emboss=False, icon="MESH_DATA")


class CG_UL_problem_frames(UIList):
    bl_idname = "CG_UL_problem_frames"

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname, index
    ):
        row = layout.row(align=True)
        row.label(text=str(getattr(item, "frame", "?")), icon="TIME")
        row.label(text=f"Clipping: {getattr(item, 'clipping_verts', 0)}")


class CG_PT_main(Panel):
    bl_label = "Cloth Guard"
    bl_idname = "CG_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Cloth Guard"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.cg_settings

        box = layout.box()
        box.label(text="Object Assignment")
        box.prop(settings, "body_object")

        box.label(text="Garments")
        row = box.row()
        row.template_list(
            "CG_UL_garments",
            "",
            settings,
            "garments",
            settings,
            "active_garment_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("cloth_guard.add_selected_garments", text="", icon="ADD")
        col.operator("cloth_guard.remove_active_garment", text="", icon="REMOVE")
        col.separator()
        col.operator("cloth_guard.move_garment", text="", icon="TRIA_UP").direction = "UP"
        col.operator("cloth_guard.move_garment", text="", icon="TRIA_DOWN").direction = "DOWN"

        row = box.row(align=True)
        row.operator("cloth_guard.add_selected_garments", text="Add Selected Garment(s)")
        row.operator("cloth_guard.remove_active_garment", text="Remove")

        box = layout.box()
        box.label(text="Post-Animation Cleanup")
        row = box.row(align=True)
        row.prop(settings, "scan_start_frame")
        row.prop(settings, "scan_end_frame")
        box.prop(settings, "scan_frame_step")
        box.operator("cloth_guard.check_garment_compatibility", text="Check Garment Compatibility")
        box.prop(settings, "ignore_topology_modifiers")

        row = box.row(align=True)
        row.operator("cloth_guard.scan_animation", text="Scan Animation")
        row.operator("cloth_guard.clear_problem_frames", text="Clear List")

        row = box.row()
        row.template_list(
            "CG_UL_problem_frames",
            "",
            settings,
            "problem_frames",
            settings,
            "active_problem_frame_index",
            rows=6,
        )
        col = row.column(align=True)
        col.operator("cloth_guard.go_to_problem_frame", text="", icon="FRAME_PREV")

        row = box.row(align=True)
        row.operator("cloth_guard.go_to_problem_frame", text="Go To Problem Frame")
        row.operator("cloth_guard.generate_correction_current_frame", text="Generate Correction (Current)")
        row = box.row(align=True)
        row.operator("cloth_guard.generate_corrections_flagged_frames", text="Generate Corrections (All Flagged)")

        if 0 <= settings.active_problem_frame_index < len(settings.problem_frames):
            item = settings.problem_frames[settings.active_problem_frame_index]
            if getattr(item, "details", ""):
                box.label(text=item.details, icon="INFO")

        box = layout.box()
        box.label(text="Setup")
        row = box.row(align=True)
        row.operator("cloth_guard.setup", text="Setup Cloth Guard")
        row.operator("cloth_guard.remove_setup", text="Remove Setup")

        box = layout.box()
        box.label(text="Detection / Correction")
        row = box.row(align=True)
        row.operator("cloth_guard.detect_clipping", text="Detect Clipping")
        row.operator("cloth_guard.select_clipping_vertices", text="Select")
        row.operator("cloth_guard.correct_current_pose", text="Update Live Corrective")
        box.prop(settings, "enable_live_anti_clip", toggle=True)
        box.operator("cloth_guard.refresh_live_correction", text="Refresh Live Correction")
        box.operator("cloth_guard.clear_live_correction", text="Clear Live Correction")

        box = layout.box()
        box.label(text="Body Mask")
        row = box.row(align=True)
        row.operator("cloth_guard.create_body_mask", text="Create Body Mask")
        row.operator("cloth_guard.delete_body_mask", text="Delete Body Mask")

        box = layout.box()
        box.label(text="Correctives")
        box.prop(settings, "corrective_name")
        box.operator("cloth_guard.create_corrective_shapekey", text="Create Corrective Shape Key")

        col = box.column(align=True)
        col.prop(settings, "driver_enable")
        sub = col.column(align=True)
        sub.enabled = settings.driver_enable
        sub.prop(settings, "driver_armature")
        sub.prop(settings, "driver_bone")
        row = sub.row(align=True)
        row.prop(settings, "driver_axis", expand=True)
        row = sub.row(align=True)
        row.prop(settings, "driver_min_angle")
        row.prop(settings, "driver_max_angle")

        box.operator("cloth_guard.bake_corrections", text="Bake Corrections")

        box = layout.box()
        box.label(text="Settings")
        col = box.column(align=True)
        col.prop(settings, "offset_distance")
        col.prop(settings, "detection_radius")
        col.prop(settings, "correction_strength")
        col.prop(settings, "max_push_distance")
        col.separator()
        col.prop(settings, "correction_passes")
        col.prop(settings, "safety_margin")
        col.prop(settings, "push_multiplier")
        col.separator()
        col.prop(settings, "smooth_iterations")
        col.prop(settings, "smooth_strength")
        col.separator()
        col.prop(settings, "mask_distance")
        col.prop(settings, "mask_expand")
        col.separator()
        col.prop(settings, "use_risk_area")
        col.prop(settings, "preserve_pinned_areas")
