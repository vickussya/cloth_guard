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

"""
All Cloth Guard operators, collected in CLASSES in registration order.

Keep every bl_idname under the cloth_guard.* namespace and never rename one: artists' keymaps
and scripts call them by id.
"""

from __future__ import annotations

from .animation import (
    CG_OT_clear_problem_frames,
    CG_OT_generate_correction_current_frame,
    CG_OT_generate_corrections_flagged_frames,
    CG_OT_go_to_problem_frame,
    CG_OT_scan_animation,
)
from .correction import (
    CG_OT_bake_corrections,
    CG_OT_clear_live_correction,
    CG_OT_correct_current_pose,
    CG_OT_create_corrective_shapekey,
    CG_OT_refresh_live_correction,
)
from .detection import (
    CG_OT_detect_clipping,
    CG_OT_detect_self_clipping,
    CG_OT_select_clipping_vertices,
    CG_OT_select_self_clipping_vertices,
)
from .garments import (
    CG_OT_add_selected_garments,
    CG_OT_check_garment_compatibility,
    CG_OT_move_garment,
    CG_OT_remove_active_garment,
)
from .masking import CG_OT_create_coverage_mask
from .setup import CG_OT_create_body_mask, CG_OT_delete_body_mask, CG_OT_remove_setup, CG_OT_setup
from .shape_preservation import (
    CG_OT_analyze_shape_drift,
    CG_OT_generate_shape_preservation_current,
    CG_OT_generate_shape_preservation_flagged,
    CG_OT_store_rest_shape,
)

# Same order as before the split (it is also the order they are shown in the operator search).
CLASSES = (
    CG_OT_add_selected_garments,
    CG_OT_remove_active_garment,
    CG_OT_move_garment,
    CG_OT_setup,
    CG_OT_remove_setup,
    CG_OT_create_body_mask,
    CG_OT_delete_body_mask,
    CG_OT_create_coverage_mask,
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
)
