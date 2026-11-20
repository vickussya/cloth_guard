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

"""Names Blender data is stored under (CG_* vertex groups, modifiers, shape keys) and tuning constants.

The CG_* names are saved in users' .blend files: never rename them."""

from __future__ import annotations

import bpy


CG_VG_BODY_MASK = "CG_BodyMask"
CG_VG_CONTACT = "CG_Contact"
CG_VG_CLIPPING = "CG_Clipping"
CG_VG_SELF_CLIPPING = "CG_SelfClipping"

CG_VG_RISK = "CG_RiskArea"
CG_VG_PINNED = "CG_Pinned"
CG_VG_PRESERVE_COLLAR = "CG_Preserve_Collar"
CG_VG_PRESERVE_HEM = "CG_Preserve_Hem"
CG_VG_PRESERVE_SEAMS = "CG_Preserve_Seams"

CG_MOD_BODY_MASK = "CG_BodyMask"
CG_MOD_ANTICLIP = "CG_AntiClip"
CG_MOD_SMOOTH = "CG_Smooth"
CG_MOD_SHAPE_PRESERVE = "CG_ShapePreserve"

CG_SHAPEKEY_LIVE = "CG_LiveCorrection"
CG_SHAPEKEY_LIVE_PRESERVE = "CG_LivePreserve"
CG_SHAPEKEY_REST = "CG_RestShape"
CG_VG_SHAPE_DRIFT = "CG_ShapeDrift"
CG_VG_SHAPE_PRESERVE = "CG_ShapePreserve"


# --- Tuning constants (values unchanged from the original inline numbers) -------------------------

# Clipping detection
PENETRATION_EPS_FRACTION = 0.05  # a vertex only counts as "inside" when deeper than 5% of the offset (noisy normals)
DETECT_PRESERVE_CONTACT_SCALE = 0.75  # how much CG_Preserve_* groups (collar/hem/seams) lower contact weights
DETECT_PRESERVE_CLIPPING_SCALE = 0.25  # how much CG_Preserve_* groups lower clipping weights
BOUNDARY_CONTACT_SCALE = 0.1  # how much open edges (hem, collar) are protected from false contact hits
BOUNDARY_CLIPPING_SCALE = 0.25  # how much open edges (hem, collar) are protected from false clipping hits
OVERLAP_NEAR_SURFACE_FRACTION = 0.15  # an overlapping triangle counts as clipping if within 15% of the offset of the body
OVERLAP_BORDER_MIN_WEIGHT = 0.9  # border vertices are escalated by the overlap test only if already this strongly clipping
DETECT_DEBUG_SAMPLES = 3  # how many sample vertices detect_clipping prints with Debug Logging on

# Anti-clip correction (shape-key path and the Geometry Nodes push-out)
CORRECTION_BOUNDARY_SCALE = 0.25  # open edges (hem, collar) are pushed only 25% as far
CORRECTION_PRESERVE_SCALE = 0.5  # at full CG_Preserve_* weight the push is halved

# Shape preservation
PRESERVE_BOUNDARY_SCALE = 0.25  # open edges get 25% of the preservation correction
PRESERVE_GROUP_SCALE = 0.75  # at full CG_Preserve_* weight the preservation correction is reduced by 75%
PRESERVE_MAX_DRIFT_REMOVAL = 0.75  # never remove more than 75% of a vertex's drift (avoids shrinking the garment)
DRIFT_SIMILARITY_RANGE = 4.0  # neighbours whose drift differs by 4x the drift threshold no longer smooth each other
MASK_PRESERVE_GROUP_SCALE = 0.9  # visual-mode mask: CG_Preserve_* groups keep 10% of the preservation

# Self-clipping detection
SELF_CLIP_PROBE_FRACTION = 0.25  # probe points sit 25% of the radius off the surface on both sides

# Temporary shape key used to measure how the modifiers move the garment (always removed again).
CG_SHAPEKEY_PROBE = "CG__DeformProbe"

# Probe step as a fraction of the garment's rest bounding-box diagonal (0.1%). Small enough that
# non-linear modifiers (Corrective Smooth, Shrinkwrap) still respond linearly, large enough to stay
# far above float32 rounding of the evaluated positions (~7 significant digits).
DEFORM_PROBE_STEP_FRACTION = 1e-3

# Matrices this close to "flat" (determinant = how much a tiny cube's volume changes) cannot be undone.
DEFORM_MIN_DETERMINANT = 1e-6

# If undoing the deformation would make the push more than this many times longer, don't trust it.
DEFORM_MAX_AMPLIFICATION = 10.0

# Geometry Nodes push-out needs "Sample Nearest Surface" (added in Blender 3.4); older versions use Displace.
ANTICLIP_GN_MIN_VERSION = (3, 4, 0)

# Modifiers cannot store custom properties (Displace never could, Geometry Nodes not since Blender 5.2),
# so the anti-clip strength base lives on the garment object.
CG_PROP_ANTICLIP_STRENGTH_BASE = "cg_anticlip_strength_base"

# Blender 5.2 replaced mod["Socket_N"] with mod.properties.inputs.Socket_N.value on Geometry Nodes
# modifiers (and modifiers no longer accept custom properties at all).
GN_INPUTS_VIA_PROPERTIES = bpy.app.version >= (5, 2, 0)

# "Never keep a worse correction" loops (values unchanged from the original inline numbers)
MAX_CORRECTION_PASSES = 5  # upper limit for the Correction Passes setting
ATTEMPTS_PER_PASS = 4  # tries per pass before a pass is rejected
HELPER_VERIFY_ATTEMPTS = 5  # tries for the Geometry Nodes helper before it is switched off
RETRY_STRENGTH_FACTOR = 0.5  # every rejected try halves the strength
SCAN_LOG_EVERY_STEPS = 10  # Scan Animation prints a debug line every 10 frame steps
PROBLEM_FRAME_DETAILS_MAX_CHARS = 1024  # same as maxlen of CG_ProblemFrameItem.details
