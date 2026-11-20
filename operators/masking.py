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

"""Coverage mask operator: hide body skin that the garments cover over the whole animation."""

from __future__ import annotations

import time
import traceback

from bpy.types import Operator

from ..constants import CG_VG_BODY_MASK
from ..core.coverage_mask import compute_coverage_mask, coverage_frames
from ..core.debug import log_debug
from ..core.mesh_utils import write_weights_to_vertex_group
from ..core.modifiers import ensure_body_mask_modifier
from ._common import _settings, _temporary_mode_object, _validate_assigned_meshes


class CG_OT_create_coverage_mask(Operator):
    bl_idname = "cloth_guard.create_coverage_mask"
    bl_label = "Create Coverage Mask (Animation)"
    bl_description = (
        "Animation: hide body skin that the garments cover on every frame of the Start-End range "
        "(hides skin poking through tight clothes between garment vertices). "
        "Skin visible on any checked frame stays visible. Replaces the CG_BodyMask group"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        frames = coverage_frames(
            int(settings.scan_start_frame), int(settings.scan_end_frame), int(settings.coverage_frame_step)
        )
        wm = context.window_manager
        started = time.perf_counter()
        with _temporary_mode_object(context):  # vertex group writes need Object Mode
            wm.progress_begin(0, len(frames))  # progress counter so Blender does not look frozen
            try:
                result = compute_coverage_mask(
                    scene=context.scene,
                    depsgraph=context.evaluated_depsgraph_get(),
                    body_obj=body_obj,
                    garments=garments,
                    frames=frames,
                    outward_distance=float(settings.coverage_outward_distance),
                    inward_distance=float(settings.coverage_inward_distance),
                    mode=str(settings.coverage_mode),
                    protect_group=str(settings.coverage_protect_group).strip(),
                    progress=wm.progress_update,
                )
            except Exception as e:  # one computation for the whole body: report it, keep the old mask
                # The frame and the CG_BodyMask modifier were already restored by compute_coverage_mask.
                self.report({"ERROR"}, f"Coverage mask failed ({type(e).__name__}: {e}). The existing mask was not changed.")
                log_debug(context, "[Cloth Guard][Error]", traceback.format_exc())
                return {"CANCELLED"}
            finally:
                wm.progress_end()

            write_weights_to_vertex_group(body_obj, CG_VG_BODY_MASK, [1.0 if m else 0.0 for m in result.masked])
            ensure_body_mask_modifier(body_obj)

        total = len(result.masked)
        percent = 100.0 * result.masked_count / total if total else 0.0
        protect_txt = f", {result.protected_count} kept visible by the protect group" if result.protected_count else ""
        mode_txt = "covered on all frames" if settings.coverage_mode == "ALL_FRAMES" else "covered on any frame"
        self.report(
            {"INFO"},
            f"Coverage mask: {result.masked_count} / {total} body vertices hidden ({percent:.1f}%), "
            f"{len(frames)} frames checked ({frames[0]}-{frames[-1]}, step {settings.coverage_frame_step}), "
            f"{mode_txt}{protect_txt}, {time.perf_counter() - started:.1f} s",
        )
        return {"FINISHED"}
