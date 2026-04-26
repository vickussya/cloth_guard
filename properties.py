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
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Object, PropertyGroup
from bpy.types import Collection

from .utils import cg_update_modifier_visibility


def _poll_mesh_object(self, obj: Object | None) -> bool:
    return obj is not None and obj.type == "MESH"


def _poll_armature_object(self, obj: Object | None) -> bool:
    return obj is not None and obj.type == "ARMATURE"


class CG_GarmentItem(PropertyGroup):
    object: PointerProperty(
        name="Garment",
        type=Object,
        poll=_poll_mesh_object,
        description="Garment mesh object to process",
    )

    enabled: BoolProperty(
        name="Enabled",
        description="Enable processing for this garment",
        default=True,
    )


class CG_ProblemFrameItem(PropertyGroup):
    frame: IntProperty(
        name="Frame",
        description="Frame number where clipping was detected",
        default=1,
        min=1,
    )

    contact_verts: IntProperty(
        name="Contact Verts",
        description="Total near-body vertices (CG_Contact) at this frame (sum across garments)",
        default=0,
        min=0,
    )

    clipping_verts: IntProperty(
        name="Clipping Verts",
        description="Total likely-penetrating vertices (CG_Clipping) at this frame (sum across garments)",
        default=0,
        min=0,
    )

    min_distance: FloatProperty(
        name="Min Distance",
        description="Minimum nearest distance found at this frame",
        default=0.0,
        min=0.0,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    details: StringProperty(
        name="Details",
        description="Per-garment summary for this frame",
        default="",
        maxlen=1024,
    )


class CG_Settings(PropertyGroup):
    body_object: PointerProperty(
        name="Body Object",
        type=Object,
        poll=_poll_mesh_object,
        description="Character body mesh (used for masking and proximity checks)",
    )

    garments: CollectionProperty(
        name="Garments",
        type=CG_GarmentItem,
        description="List of garment mesh objects to process",
    )

    active_garment_index: IntProperty(
        name="Active Garment Index",
        default=0,
        min=0,
    )

    scan_start_frame: IntProperty(
        name="Start Frame",
        description="Start frame for Scan Animation",
        default=1,
        min=1,
    )

    scan_end_frame: IntProperty(
        name="End Frame",
        description="End frame for Scan Animation",
        default=250,
        min=1,
    )

    scan_frame_step: IntProperty(
        name="Frame Step",
        description="Frame step for scanning (higher = faster, less precise)",
        default=1,
        min=1,
        max=1000,
    )

    problem_frames: CollectionProperty(
        name="Problem Frames",
        type=CG_ProblemFrameItem,
        description="Frames flagged during Scan Animation",
    )

    active_problem_frame_index: IntProperty(
        name="Active Problem Frame Index",
        default=0,
        min=0,
    )

    # Legacy multi-garment support (kept for older .blend files; UI prefers garment list).
    garment_collection: PointerProperty(
        name="Garment Collection (Legacy)",
        type=Collection,
        description="Legacy collection-based garment assignment (use Garments list instead)",
    )

    # Legacy single-garment support (kept for older .blend files; UI prefers garment list).
    garment_object: PointerProperty(
        name="Garment Object (Legacy)",
        type=Object,
        poll=_poll_mesh_object,
        description="Legacy single garment mesh (use Garments list instead)",
    )

    enable_live_anti_clip: BoolProperty(
        name="Enable Live Anti-Clip",
        description="Enable/disable Cloth Guard's live correction shape key on garments (refresh as needed)",
        default=False,
        update=lambda self, context: cg_update_modifier_visibility(context),
    )

    offset_distance: FloatProperty(
        name="Offset Distance",
        description="Target minimum offset distance from the body surface",
        default=0.005,
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
        default=0.03,
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

    correction_passes: IntProperty(
        name="Correction Passes",
        description="Number of gentle correction passes to run (more passes can remove residual clipping without a single aggressive deformation)",
        default=2,
        min=1,
        max=5,
    )

    safety_margin: FloatProperty(
        name="Safety Margin",
        description="Extra margin added to Offset Distance during correction (helps reduce residual clipping)",
        default=0.001,
        min=0.0,
        soft_max=0.02,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    push_multiplier: FloatProperty(
        name="Push Multiplier",
        description="Multiplier on correction strength (kept controlled; higher values push a bit more per pass)",
        default=1.0,
        min=0.0,
        max=3.0,
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

    ignore_topology_modifiers: BoolProperty(
        name="Use Cage Mode For Topology-Changing Garments",
        description="When a garment has topology-changing modifiers (Subdivision/Geometry Nodes/etc.), Cloth Guard computes detection/corrections on a topology-stable cage (temporarily disabling those modifiers) and applies the result non-destructively through the live modifier stack",
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
