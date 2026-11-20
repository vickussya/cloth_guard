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

"""Animation operators: scan, problem frames, generate corrections for current / flagged frames."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..constants import (
    CG_MOD_ANTICLIP,
    CG_SHAPEKEY_LIVE,
    PROBLEM_FRAME_DETAILS_MAX_CHARS,
    SCAN_LOG_EVERY_STEPS,
)
from ..core.body import build_body_surface
from ..core.debug import log_debug
from ..core.modifiers import ensure_anticlip_modifiers
from ._common import (
    _report_garment_error,
    _run_detection_and_weights,
    _settings,
    _shapekey_delta_stats,
    _temporary_mode_object,
    _topology_mismatch,
    _validate_assigned_meshes,
)
from ._keyframes import _keyframe_modifier_strength, _keyframe_shapekey_value
from ._passes import _bake_live_correction_to_key, _run_shape_key_passes, _verify_helper_correction


class CG_OT_scan_animation(Operator):
    bl_idname = "cloth_guard.scan_animation"
    bl_label = "Scan Animation"
    bl_description = "Scans the selected frame range and lists frames where clipping is detected"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        start = int(settings.scan_start_frame)
        end = int(settings.scan_end_frame)
        step = int(settings.scan_frame_step)
        if step <= 0:
            step = 1
        if end < start:
            start, end = end, start

        settings.problem_frames.clear()
        settings.active_problem_frame_index = 0

        scene = context.scene
        prev_frame = int(scene.frame_current)
        flagged_frames = 0

        frames = list(range(start, end + 1, step))
        wm = context.window_manager
        wm.progress_begin(0, len(frames))  # progress counter so Blender does not look frozen
        try:
            for frame_index, frame in enumerate(frames):
                wm.progress_update(frame_index)
                scene.frame_set(frame)
                depsgraph = context.evaluated_depsgraph_get()
                body_surface = build_body_surface(body_obj, depsgraph)  # once per frame, shared by every garment

                total_contact = 0
                total_clipping = 0
                min_dist = None
                details_parts: list[str] = []

                for garment_obj in garments:
                    try:
                        res, _, _ = _run_detection_and_weights(
                            context=context,
                            settings=settings,
                            garment_obj=garment_obj,
                            body_obj=body_obj,
                            depsgraph=depsgraph,
                            body_surface=body_surface,
                            offset_distance=max(0.0, float(settings.offset_distance + settings.safety_margin)),
                            detection_radius=max(float(settings.offset_distance + settings.safety_margin), settings.detection_radius),
                        )
                    except Exception as e:  # per-garment boundary, see _report_garment_error
                        details_parts.append(f"{garment_obj.name}:ERR")
                        _report_garment_error(self, context, garment_obj, "scan", e, frame=frame)
                        continue

                    s = res.stats
                    total_contact += int(s.flagged_contact)
                    total_clipping += int(s.flagged_clipping)
                    if s.min_nearest_distance is not None:
                        min_dist = s.min_nearest_distance if min_dist is None else min(min_dist, s.min_nearest_distance)
                    if s.flagged_clipping > 0:
                        details_parts.append(f"{garment_obj.name}:{int(s.flagged_clipping)}")

                if total_clipping > 0:
                    item = settings.problem_frames.add()
                    item.frame = frame
                    item.contact_verts = int(total_contact)
                    item.clipping_verts = int(total_clipping)
                    item.min_distance = float(min_dist) if min_dist is not None else 0.0
                    item.details = ", ".join(details_parts)[:PROBLEM_FRAME_DETAILS_MAX_CHARS]
                    flagged_frames += 1

                if frame == start or frame == end or (frame - start) % max(step * SCAN_LOG_EVERY_STEPS, 1) == 0:
                    min_txt = f"{min_dist:.6f} m" if min_dist is not None else "n/a"
                    log_debug(
                        context,
                        "[Cloth Guard][Scan]",
                        f"frame={frame}",
                        f"contact={total_contact}",
                        f"clipping={total_clipping}",
                        f"min={min_txt}",
                    )
        finally:
            wm.progress_end()
            scene.frame_set(prev_frame)

        self.report({"INFO"}, f"Scan complete: {flagged_frames} problem frame(s) flagged ({start}-{end} step {step})")
        return {"FINISHED"}


class CG_OT_clear_problem_frames(Operator):
    bl_idname = "cloth_guard.clear_problem_frames"
    bl_label = "Clear Problem Frames"
    bl_description = "Clear the problem frames list"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        settings.problem_frames.clear()
        settings.active_problem_frame_index = 0
        self.report({"INFO"}, "Problem frames cleared")
        return {"FINISHED"}


class CG_OT_go_to_problem_frame(Operator):
    bl_idname = "cloth_guard.go_to_problem_frame"
    bl_label = "Go To Problem Frame"
    bl_description = "Jump the timeline to the selected problem frame"
    bl_options = {"REGISTER", "UNDO"}

    frame: bpy.props.IntProperty(name="Frame", default=-1, min=-1)

    def execute(self, context):
        settings = _settings(context)
        scene = context.scene

        target = int(self.frame)
        if target < 0:
            idx = int(settings.active_problem_frame_index)
            if idx < 0 or idx >= len(settings.problem_frames):
                self.report({"ERROR"}, "Select a problem frame first")
                return {"CANCELLED"}
            target = int(settings.problem_frames[idx].frame)

        scene.frame_set(target)
        self.report({"INFO"}, f"Set frame to {target}")
        return {"FINISHED"}


class CG_OT_generate_correction_current_frame(Operator):
    bl_idname = "cloth_guard.generate_correction_current_frame"
    bl_label = "Generate Correction For Current Frame"
    bl_description = "Creates a non-destructive anti-clipping correction for the current frame"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        frame = int(context.scene.frame_current)
        depsgraph = context.evaluated_depsgraph_get()
        body_surface = build_body_surface(body_obj, depsgraph)  # once per frame, shared by every garment

        view_layer = context.view_layer
        prev_active = view_layer.objects.active

        baked = 0
        with _temporary_mode_object(context):
            for garment_obj in garments:
                try:
                    target_offset = max(0.0, float(settings.offset_distance + settings.safety_margin))
                    det, _, clipping_affected = _run_detection_and_weights(
                        context=context,
                        settings=settings,
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        body_surface=body_surface,
                        offset_distance=target_offset,
                        detection_radius=max(target_offset, settings.detection_radius),
                    )

                    # Topology mismatch: bake as helper/modifier strength keyframes instead of shape keys.
                    if _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph):
                        ensure_anticlip_modifiers(
                            garment_obj=garment_obj,
                            body_obj=body_obj,
                            offset_distance=target_offset,
                            smooth_iterations=settings.smooth_iterations,
                            smooth_strength=settings.smooth_strength,
                            mode=str(getattr(settings, "anticlip_mode", "SAFE_PUSHOUT")),
                            strength=max(0.0, min(1.0, float(settings.correction_strength) * float(settings.push_multiplier))),
                            max_push_distance=float(settings.max_push_distance),
                        )
                        accepted_factor, det_after = _verify_helper_correction(
                            context=context,
                            settings=settings,
                            garment_obj=garment_obj,
                            body_obj=body_obj,
                            depsgraph=depsgraph,
                            body_surface=body_surface,
                            det_before=det,
                            target_offset=target_offset,
                        )
                        _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame - 1, value=0.0)
                        _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame, value=float(accepted_factor))
                        _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame + 1, value=0.0)
                        baked += 1
                        log_debug(
                            context,
                            "[Cloth Guard][BakeFrame][Helper]",
                            garment_obj.name,
                            f"frame={frame}",
                            f"clipping={int(det.stats.flagged_clipping)}",
                            f"after={int(det_after.stats.flagged_clipping)}",
                            f"accepted_factor={accepted_factor:.3f}",
                            f"clipping_vg={clipping_affected}",
                            f"mod={CG_MOD_ANTICLIP}",
                        )
                        continue

                    det_before, det_after, used = _run_shape_key_passes(
                        context=context,
                        settings=settings,
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        body_surface=body_surface,
                        report_to=self,
                    )
                    changed_live, max_live = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=CG_SHAPEKEY_LIVE)
                    if changed_live <= 0 or max_live <= 1e-12:
                        self.report({"INFO"}, f"{garment_obj.name}: no correction delta generated at frame {frame}")
                        continue
                    view_layer.objects.active = garment_obj
                    key_name = f"CG_Frame_{frame:04d}"
                    _bake_live_correction_to_key(garment_obj, key_name)
                    _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame - 1, value=0.0)
                    _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame, value=1.0)
                    _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame + 1, value=0.0)
                    changed, max_d = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=key_name)
                    log_debug(
                        context,
                        "[Cloth Guard][BakeFrame]",
                        garment_obj.name,
                        f"frame={frame}",
                        f"before={int(det_before.stats.flagged_clipping)}",
                        f"after={int(det_after.stats.flagged_clipping)}",
                        f"passes={used}",
                        f"delta_verts={changed}",
                        f"max_delta={max_d:.6f}",
                        f"key={key_name}",
                    )
                    baked += 1
                except Exception as e:  # per-garment boundary, see _report_garment_error
                    _report_garment_error(self, context, garment_obj, "correction bake", e, frame=frame)
            settings.enable_live_anti_clip = True

        view_layer.objects.active = prev_active
        self.report({"INFO"}, f"Baked per-frame correctives on {baked} garment(s) at frame {frame}")
        return {"FINISHED"}


class CG_OT_generate_corrections_flagged_frames(Operator):
    bl_idname = "cloth_guard.generate_corrections_flagged_frames"
    bl_label = "Generate Corrections For All Flagged Frames"
    bl_description = "Batch: creates non-destructive anti-clipping corrections for all flagged frames"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        if len(settings.problem_frames) == 0:
            self.report({"ERROR"}, "No problem frames stored. Run Scan Animation first.")
            return {"CANCELLED"}

        scene = context.scene
        prev_frame = int(scene.frame_current)

        view_layer = context.view_layer
        prev_active = view_layer.objects.active

        frames = [int(it.frame) for it in settings.problem_frames]
        frames = sorted(set(frames))

        baked_keys = 0
        wm = context.window_manager
        wm.progress_begin(0, len(frames))  # progress counter so Blender does not look frozen
        try:
            with _temporary_mode_object(context):
                for frame_index, frame in enumerate(frames):
                    wm.progress_update(frame_index)
                    scene.frame_set(frame)
                    depsgraph = context.evaluated_depsgraph_get()
                    body_surface = build_body_surface(body_obj, depsgraph)  # once per frame, shared by every garment
                    for garment_obj in garments:
                        try:
                            target_offset = max(0.0, float(settings.offset_distance + settings.safety_margin))
                            det, _, clipping_affected = _run_detection_and_weights(
                                context=context,
                                settings=settings,
                                garment_obj=garment_obj,
                                body_obj=body_obj,
                                depsgraph=depsgraph,
                                body_surface=body_surface,
                                offset_distance=target_offset,
                                detection_radius=max(target_offset, settings.detection_radius),
                            )

                            if _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph):
                                ensure_anticlip_modifiers(
                                    garment_obj=garment_obj,
                                    body_obj=body_obj,
                                    offset_distance=target_offset,
                                    smooth_iterations=settings.smooth_iterations,
                                    smooth_strength=settings.smooth_strength,
                                    mode=str(getattr(settings, "anticlip_mode", "SAFE_PUSHOUT")),
                                    strength=max(0.0, min(1.0, float(settings.correction_strength) * float(settings.push_multiplier))),
                                    max_push_distance=float(settings.max_push_distance),
                                )
                                accepted_factor, det_after = _verify_helper_correction(
                                    context=context,
                                    settings=settings,
                                    garment_obj=garment_obj,
                                    body_obj=body_obj,
                                    depsgraph=depsgraph,
                                    body_surface=body_surface,
                                    det_before=det,
                                    target_offset=target_offset,
                                )
                                _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame - 1, value=0.0)
                                _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame, value=float(accepted_factor))
                                _keyframe_modifier_strength(garment_obj=garment_obj, mod_name=CG_MOD_ANTICLIP, frame=frame + 1, value=0.0)
                                baked_keys += 1
                                log_debug(
                                    context,
                                    "[Cloth Guard][BakeFlagged][Helper]",
                                    garment_obj.name,
                                    f"frame={frame}",
                                    f"clipping={int(det.stats.flagged_clipping)}",
                                    f"after={int(det_after.stats.flagged_clipping)}",
                                    f"accepted_factor={accepted_factor:.3f}",
                                    f"clipping_vg={clipping_affected}",
                                    f"mod={CG_MOD_ANTICLIP}",
                                )
                                continue

                            det_before, det_after, used = _run_shape_key_passes(
                                context=context,
                                settings=settings,
                                garment_obj=garment_obj,
                                body_obj=body_obj,
                                depsgraph=depsgraph,
                                body_surface=body_surface,
                                report_to=self,
                            )
                            changed_live, max_live = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=CG_SHAPEKEY_LIVE)
                            if changed_live <= 0 or max_live <= 1e-12:
                                log_debug(context, "[Cloth Guard][BakeFlagged]", garment_obj.name, f"frame={frame}", "NO_DELTA")
                                continue
                            view_layer.objects.active = garment_obj
                            key_name = f"CG_Frame_{frame:04d}"
                            _bake_live_correction_to_key(garment_obj, key_name)
                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame - 1, value=0.0)
                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame, value=1.0)
                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame + 1, value=0.0)
                            changed, max_d = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=key_name)
                            log_debug(
                                context,
                                "[Cloth Guard][BakeFlagged]",
                                garment_obj.name,
                                f"frame={frame}",
                                f"before={int(det_before.stats.flagged_clipping)}",
                                f"after={int(det_after.stats.flagged_clipping)}",
                                f"passes={used}",
                                f"delta_verts={changed}",
                                f"max_delta={max_d:.6f}",
                                f"key={key_name}",
                            )
                            baked_keys += 1
                        except Exception as e:  # per-garment boundary, see _report_garment_error
                            _report_garment_error(self, context, garment_obj, "correction bake", e, frame=frame)
                settings.enable_live_anti_clip = True
        finally:
            wm.progress_end()
            scene.frame_set(prev_frame)
            view_layer.objects.active = prev_active

        self.report({"INFO"}, f"Generated per-frame correctives: {len(frames)} frame(s), {baked_keys} baked key(s)")
        return {"FINISHED"}
