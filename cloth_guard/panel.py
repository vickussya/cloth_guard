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
        box.prop(settings, "garment_object")

        box = layout.box()
        box.label(text="Setup")
        row = box.row(align=True)
        row.operator("cloth_guard.setup", text="Setup Cloth Guard")
        row.operator("cloth_guard.remove_setup", text="Remove Setup")

        box = layout.box()
        box.label(text="Detection / Correction")
        row = box.row(align=True)
        row.operator("cloth_guard.detect_clipping", text="Detect Clipping")
        row.operator("cloth_guard.correct_current_pose", text="Correct Current Pose")
        box.prop(settings, "enable_live_anti_clip", toggle=True)
        box.operator("cloth_guard.refresh_live_correction", text="Refresh Live Correction")

        box = layout.box()
        box.label(text="Body Mask")
        box.operator("cloth_guard.create_body_mask", text="Create Body Mask")

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
        col.prop(settings, "smooth_iterations")
        col.prop(settings, "smooth_strength")
        col.separator()
        col.prop(settings, "mask_distance")
        col.prop(settings, "mask_expand")
        col.separator()
        col.prop(settings, "use_risk_area")
        col.prop(settings, "preserve_pinned_areas")

