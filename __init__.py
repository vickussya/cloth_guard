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

bl_info = {
    "name": "Cloth Guard",
    "author": "Vickussya",
    "version": (0, 1, 0),
    "blender": (3, 0, 0),
    "location": "3D View > Sidebar > Cloth Guard",
    "description": "Post-animation garment cleanup: preserve stylized shapes and reduce body clipping without cloth simulation",
    "category": "Animation",
}

import bpy

from .operators import (
    CG_OT_add_selected_garments,
    CG_OT_bake_corrections,
    CG_OT_check_garment_compatibility,
    CG_OT_clear_live_correction,
    CG_OT_correct_current_pose,
    CG_OT_delete_body_mask,
    CG_OT_create_body_mask,
    CG_OT_store_rest_shape,
    CG_OT_analyze_shape_drift,
    CG_OT_generate_shape_preservation_current,
    CG_OT_generate_shape_preservation_flagged,
    CG_OT_create_corrective_shapekey,
    CG_OT_detect_clipping,
    CG_OT_detect_self_clipping,
    CG_OT_generate_correction_current_frame,
    CG_OT_generate_corrections_flagged_frames,
    CG_OT_go_to_problem_frame,
    CG_OT_clear_problem_frames,
    CG_OT_move_garment,
    CG_OT_remove_active_garment,
    CG_OT_select_clipping_vertices,
    CG_OT_select_self_clipping_vertices,
    CG_OT_refresh_live_correction,
    CG_OT_remove_setup,
    CG_OT_setup,
    CG_OT_scan_animation,
)
from .panels import CG_PT_main, CG_UL_garments, CG_UL_problem_frames
from .properties import (
    CG_GarmentItem,
    CG_ProblemFrameItem,
    CG_Settings,
    register_properties,
    unregister_properties,
)


CLASSES = (
    CG_GarmentItem,
    CG_ProblemFrameItem,
    CG_Settings,
    CG_OT_add_selected_garments,
    CG_OT_remove_active_garment,
    CG_OT_move_garment,
    CG_OT_setup,
    CG_OT_remove_setup,
    CG_OT_create_body_mask,
    CG_OT_delete_body_mask,
    CG_OT_store_rest_shape,
    CG_OT_analyze_shape_drift,
    CG_OT_generate_shape_preservation_current,
    CG_OT_generate_shape_preservation_flagged,
    CG_OT_detect_clipping,
    CG_OT_select_clipping_vertices,
    CG_OT_detect_self_clipping,
    CG_OT_select_self_clipping_vertices,
    CG_OT_correct_current_pose,
    CG_OT_refresh_live_correction,
    CG_OT_clear_live_correction,
    CG_OT_check_garment_compatibility,
    CG_OT_scan_animation,
    CG_OT_clear_problem_frames,
    CG_OT_go_to_problem_frame,
    CG_OT_generate_correction_current_frame,
    CG_OT_generate_corrections_flagged_frames,
    CG_OT_create_corrective_shapekey,
    CG_OT_bake_corrections,
    CG_UL_garments,
    CG_UL_problem_frames,
    CG_PT_main,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    register_properties()


def unregister():
    unregister_properties()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
