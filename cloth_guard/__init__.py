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
    "description": "Non-simulation clothing anti-clipping and shape-preservation tools for animation",
    "category": "Animation",
}

import bpy

from .operators import (
    CG_OT_bake_corrections,
    CG_OT_correct_current_pose,
    CG_OT_create_body_mask,
    CG_OT_create_corrective_shapekey,
    CG_OT_detect_clipping,
    CG_OT_refresh_live_correction,
    CG_OT_remove_setup,
    CG_OT_setup,
)
from .panels import CG_PT_main
from .properties import CG_Settings, register_properties, unregister_properties


CLASSES = (
    CG_Settings,
    CG_OT_setup,
    CG_OT_remove_setup,
    CG_OT_create_body_mask,
    CG_OT_detect_clipping,
    CG_OT_correct_current_pose,
    CG_OT_refresh_live_correction,
    CG_OT_create_corrective_shapekey,
    CG_OT_bake_corrections,
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

