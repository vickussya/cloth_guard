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
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Object, PropertyGroup

from .utils import cg_update_modifier_visibility


def _poll_mesh_object(self, obj: Object | None) -> bool:
    return obj is not None and obj.type == "MESH"


def _poll_armature_object(self, obj: Object | None) -> bool:
    return obj is not None and obj.type == "ARMATURE"


class CG_Settings(PropertyGroup):
    body_object: PointerProperty(
        name="Body Object",
        type=Object,
        poll=_poll_mesh_object,
        description="Character body mesh (used for masking and proximity checks)",
    )

    garment_object: PointerProperty(
        name="Garment Object",
        type=Object,
        poll=_poll_mesh_object,
        description="Rigged garment mesh (to detect clipping and apply corrections)",
    )

    enable_live_anti_clip: BoolProperty(
        name="Enable Live Anti-Clip",
        description="Enable/disable Cloth Guard's anti-clip modifier stack on the garment (refresh weights as needed)",
        default=False,
        update=lambda self, context: cg_update_modifier_visibility(context),
    )

    offset_distance: FloatProperty(
        name="Offset Distance",
        description="Target minimum offset distance from the body surface",
        default=0.003,
        min=0.0,
        soft_max=0.05,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    detection_radius: FloatProperty(
        name="Detection Radius",
        description="Maximum distance from the body within which vertices can be considered at risk (used for falloff)",
        default=0.02,
        min=0.0,
        soft_max=0.2,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    correction_strength: FloatProperty(
        name="Correction Strength",
        description="Overall influence multiplier for anti-clip correction (0 disables; 1 is full)",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    max_push_distance: FloatProperty(
        name="Max Push Distance",
        description="Safety limit (MVP uses it as a detection cap; future versions may use it for explicit push limits)",
        default=0.05,
        min=0.0,
        soft_max=0.5,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    smooth_iterations: IntProperty(
        name="Smooth Iterations",
        description="Smoothing iterations for localized post-correction smoothing",
        default=5,
        min=0,
        max=100,
    )

    smooth_strength: FloatProperty(
        name="Smooth Strength",
        description="Smoothing factor applied to corrected areas (higher = smoother, but can soften silhouette)",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    mask_distance: FloatProperty(
        name="Mask Distance",
        description="Distance threshold for hiding body parts under the garment",
        default=0.003,
        min=0.0,
        soft_max=0.05,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    mask_expand: FloatProperty(
        name="Expand Mask",
        description="Expand (>0) or shrink (<0) the effective mask distance",
        default=0.0,
        soft_min=-0.05,
        soft_max=0.05,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    use_risk_area: BoolProperty(
        name="Use Risk Area",
        description="If the garment has a 'CG_RiskArea' vertex group, restrict correction/detection to that area",
        default=False,
    )

    preserve_pinned_areas: BoolProperty(
        name="Preserve Pinned Areas",
        description="If the garment has a 'CG_Pinned' vertex group, reduce correction there",
        default=True,
    )

    corrective_name: StringProperty(
        name="Corrective Name",
        description="Name for the new corrective shape key",
        default="CG_Corrective",
        maxlen=128,
    )

    driver_enable: BoolProperty(
        name="Link Corrective to Pose",
        description="Create a driver for the new corrective based on a bone rotation range (MVP)",
        default=False,
    )

    driver_armature: PointerProperty(
        name="Armature",
        type=Object,
        poll=_poll_armature_object,
        description="Armature object used for driving corrective shape keys",
    )

    driver_bone: StringProperty(
        name="Bone",
        description="Bone name to drive from (must exist on the selected armature)",
        default="",
        maxlen=256,
    )

    driver_axis: EnumProperty(
        name="Axis",
        description="Bone rotation axis to use for driving (in bone local space)",
        items=(
            ("X", "X", "X axis"),
            ("Y", "Y", "Y axis"),
            ("Z", "Z", "Z axis"),
        ),
        default="X",
    )

    driver_min_angle: FloatProperty(
        name="Min Angle",
        description="Angle (radians) where the corrective starts (value 0)",
        default=0.0,
        subtype="ANGLE",
        unit="ROTATION",
    )

    driver_max_angle: FloatProperty(
        name="Max Angle",
        description="Angle (radians) where the corrective is fully on (value 1)",
        default=0.785398,  # 45 degrees in radians
        subtype="ANGLE",
        unit="ROTATION",
    )


def register_properties():
    bpy.types.Scene.cg_settings = PointerProperty(type=CG_Settings)


def unregister_properties():
    try:
        del bpy.types.Scene.cg_settings
    except Exception:
        pass

