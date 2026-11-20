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

"""Shape-key driver helpers (drive a corrective by a bone rotation range)."""

from __future__ import annotations

import math

import bpy


def add_shapekey_driver_rotation_range(
    *,
    garment_obj: bpy.types.Object,
    shapekey_name: str,
    armature_obj: bpy.types.Object,
    bone_name: str,
    axis: str,
    min_angle_rad: float,
    max_angle_rad: float,
) -> None:
    if garment_obj.data.shape_keys is None:
        raise RuntimeError("Garment has no shape keys")
    kb = garment_obj.data.shape_keys.key_blocks.get(shapekey_name)
    if kb is None:
        raise RuntimeError(f"Shape key not found: {shapekey_name}")
    if armature_obj is None or armature_obj.type != "ARMATURE":
        raise RuntimeError("Armature object is missing or not an Armature")
    if bone_name not in armature_obj.pose.bones:
        raise RuntimeError(f"Bone not found on armature: {bone_name}")

    fcurve = kb.driver_add("value")
    drv = fcurve.driver
    drv.type = "SCRIPTED"

    var = drv.variables.new()
    var.name = "r"
    var.type = "TRANSFORMS"
    tgt = var.targets[0]
    tgt.id = armature_obj
    tgt.bone_target = bone_name
    tgt.transform_space = "LOCAL_SPACE"
    if axis == "X":
        tgt.transform_type = "ROT_X"
    elif axis == "Y":
        tgt.transform_type = "ROT_Y"
    else:
        tgt.transform_type = "ROT_Z"

    if math.isclose(max_angle_rad, min_angle_rad, abs_tol=1e-8):
        drv.expression = "0.0"
        return

    min_a = float(min_angle_rad)
    max_a = float(max_angle_rad)
    denom = max_a - min_a
    drv.expression = f"max(0.0, min(1.0, (r - ({min_a})) / ({denom})))"
