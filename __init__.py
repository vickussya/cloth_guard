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
    "version": (0, 1, 1),
    "blender": (3, 0, 0),
    "location": "3D View > Sidebar > Cloth Guard",
    "description": "Post-animation garment cleanup: preserve stylized shapes and reduce body clipping without cloth simulation",
    "category": "Animation",
}

# Every submodule, in dependency order (a module comes after everything it imports).
_SUBMODULES = (
    "constants",
    "core.debug",
    "core.mesh_utils",
    "core.body",
    "core.shape_keys",
    "core.detection",
    "core.correction",
    "core.shape_preservation",
    "core.modifiers",
    "core.drivers",
    "core",
    "properties",
    "operators._common",
    "operators._keyframes",
    "operators._passes",
    "operators.garments",
    "operators.setup",
    "operators.shape_preservation",
    "operators.detection",
    "operators.correction",
    "operators.animation",
    "operators",
    "panels",
)

if "bpy" in locals():
    # F3 > "Reload Scripts" (and re-enabling after a code change) re-runs only this file.
    # Reload the submodules too, or Blender keeps running their old code.
    import importlib
    import sys

    for _name in _SUBMODULES:
        _module = sys.modules.get(f"{__name__}.{_name}")
        if _module is not None:
            importlib.reload(_module)

import bpy

from . import operators, panels, properties


def _classes() -> tuple:
    # Built when called (not at import), so a reload registers the fresh classes.
    return (
        properties.CG_GarmentItem,
        properties.CG_ProblemFrameItem,
        properties.CG_Settings,
        *operators.CLASSES,
        panels.CG_UL_garments,
        panels.CG_UL_problem_frames,
        panels.CG_PT_main,
    )


def register():
    for cls in _classes():
        bpy.utils.register_class(cls)
    properties.register_properties()


def unregister():
    properties.unregister_properties()
    for cls in reversed(_classes()):
        bpy.utils.unregister_class(cls)
