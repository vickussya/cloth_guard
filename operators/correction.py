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

"""Live correction operators (update / refresh / clear) and corrective shape key baking."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..constants import CG_MOD_ANTICLIP, CG_SHAPEKEY_LIVE
from ..core.body import build_body_surface
from ..core.correction import correct_current_pose
from ..core.debug import log_debug
from ..core.drivers import add_shapekey_driver_rotation_range
from ..core.modifiers import anticlip_strength_base, ensure_anticlip_modifiers
from ..core.shape_keys import bake_live_key_into, ensure_live_correction_shapekey
from ._common import (
    _NON_DESTRUCTIVE_MSG,
    _report_garment_error,
    _run_detection_and_weights,
    _settings,
    _shapekey_delta_stats,
    _temporary_mode_object,
    _topology_mismatch,
    _validate_assigned_meshes,
)
from ._passes import _run_shape_key_passes, _verify_helper_correction, _warn_unconvertible


class CG_OT_correct_current_pose(Operator):
    bl_idname = "cloth_guard.correct_current_pose"
    bl_label = "Update Live Corrective"
    bl_description = "Non-destructive: update the CG_LiveCorrection shape key for the current pose (Basis is never modified)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            body_surface = build_body_surface(body_obj, depsgraph)  # once per frame, shared by every garment

            total_checked = 0
            total_candidates = 0
            total_contact = 0
            total_clipping = 0
            total_corrected = 0
            global_min = None

            for garment_obj in garments:
                try:
                    target_offset = max(0.0, float(settings.offset_distance + settings.safety_margin))
                    det, contact_affected, clipping_affected = _run_detection_and_weights(
                        context=context,
                        settings=settings,
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        body_surface=body_surface,
                        offset_distance=target_offset,
                        detection_radius=max(target_offset, settings.detection_radius),
                    )
                except Exception as e:  # per-garment boundary, see _report_garment_error
                    _report_garment_error(self, context, garment_obj, "clipping detection", e)
                    continue

                # Mode selection:
                # - Topology stable: write CG_LiveCorrection (shape key)
                # - Topology changing: set up helper modifiers and refresh weights (non-destructive)
                is_mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)

                before_clipping = int(det.stats.flagged_clipping)

                if is_mismatch:
                    try:
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
                        total_corrected += int(clipping_affected)
                        s = det.stats
                        total_checked += s.checked_verts
                        total_candidates += s.candidates_within_radius
                        total_contact += contact_affected
                        total_clipping += clipping_affected
                        if s.min_nearest_distance is not None:
                            global_min = s.min_nearest_distance if global_min is None else min(global_min, s.min_nearest_distance)
                        self.report(
                            {"INFO"},
                            f"{garment_obj.name}: topology-changing modifiers detected; using helper/modifier correction mode. {_NON_DESTRUCTIVE_MSG}",
                        )
                        # Residual check (still in cage mode so mapping stays stable).
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
                        after_clipping = int(det_after.stats.flagged_clipping)
                        max_w = float(max(det.clipping_weights)) if det.clipping_weights else 0.0
                        base = anticlip_strength_base(
                            garment_obj, garment_obj.modifiers.get(CG_MOD_ANTICLIP), float(target_offset)
                        )
                        approx_max_delta = base * max_w
                        protect_txt = "ON" if bool(settings.preserve_pinned_areas) else "OFF"
                        accept_txt = "ACCEPTED" if accepted_factor > 0.0 else "REJECTED"
                        self.report(
                            {"INFO"},
                            f"{garment_obj.name}: {before_clipping} clipping before, {after_clipping} remaining (helper mode); "
                            f"max_push≈{approx_max_delta:.6f} m; mode={str(getattr(settings, 'anticlip_mode', 'SAFE_PUSHOUT'))}; "
                            f"smoothing={'ON' if str(getattr(settings, 'anticlip_mode', 'SAFE_PUSHOUT'))=='SMART' else 'OFF'}; "
                            f"shrink_protect={protect_txt}; verify={accept_txt}",
                        )
                    except Exception as e:  # per-garment boundary, see _report_garment_error
                        _report_garment_error(self, context, garment_obj, "helper/modifier correction", e, level="ERROR")
                    continue

                try:
                    det_before, det_after, used = _run_shape_key_passes(
                        context=context,
                        settings=settings,
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        body_surface=body_surface,
                        report_to=self,
                    )
                    after_clipping = int(det_after.stats.flagged_clipping)
                    changed_live, max_live = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=CG_SHAPEKEY_LIVE)
                    protect_txt = "ON" if bool(settings.preserve_pinned_areas) else "OFF"
                    self.report(
                        {"INFO"},
                        f"{garment_obj.name}: {before_clipping} clipping before, {after_clipping} remaining after {used} pass(es); "
                        f"delta_verts={changed_live}; max_delta={max_live:.6f} m; mode={str(getattr(settings, 'anticlip_mode', 'SAFE_PUSHOUT'))}; "
                        f"smoothing={'ON' if str(getattr(settings, 'anticlip_mode', 'SAFE_PUSHOUT'))=='SMART' else 'OFF'}; "
                        f"shrink_protect={protect_txt}",
                    )
                except Exception as e:  # per-garment boundary, see _report_garment_error
                    _report_garment_error(self, context, garment_obj, "correction", e, level="ERROR")
                    continue

                # Update global summary using before detection stats (stable) and after for min.
                s = det.stats
                total_checked += s.checked_verts
                total_candidates += s.candidates_within_radius
                total_contact += contact_affected
                total_clipping += clipping_affected
                total_corrected += int(before_clipping - after_clipping) if before_clipping >= after_clipping else 0
                if det_after.stats.min_nearest_distance is not None:
                    global_min = (
                        det_after.stats.min_nearest_distance
                        if global_min is None
                        else min(global_min, det_after.stats.min_nearest_distance)
                    )

            settings.enable_live_anti_clip = True
            global_min_txt = f"{global_min:.6f} m" if global_min is not None else "n/a"
            msg = (
                f"Processed {len(garments)} garment(s); checked {total_checked} verts; "
                f"{total_candidates} within radius; {total_contact} contact; {total_clipping} clipping; corrected {total_corrected}; "
                f"min distance {global_min_txt}"
            )
            log_debug(context, "[Cloth Guard][Correct]", msg)
            self.report({"INFO"}, msg)

        return {"FINISHED"}


class CG_OT_clear_live_correction(Operator):
    bl_idname = "cloth_guard.clear_live_correction"
    bl_label = "Clear Live Correction"
    bl_description = "Non-destructive: reset CG_LiveCorrection to match Basis (removes the live correction effect)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        with _temporary_mode_object(context):
            cleared = 0
            for garment_obj in garments:
                if garment_obj.type != "MESH":
                    continue
                try:
                    kb = ensure_live_correction_shapekey(garment_obj)
                    basis = garment_obj.data.shape_keys.key_blocks[0]
                    if len(kb.data) != len(basis.data):
                        raise RuntimeError("Shape key vertex count mismatch")
                    for i in range(len(basis.data)):
                        kb.data[i].co = basis.data[i].co
                    cleared += 1
                except Exception as e:  # per-garment boundary, see _report_garment_error
                    _report_garment_error(self, context, garment_obj, "clearing the live correction", e)

            self.report({"INFO"}, f"Cleared CG_LiveCorrection on {cleared} garment(s) (Basis unchanged)")
        return {"FINISHED"}


class CG_OT_refresh_live_correction(Operator):
    bl_idname = "cloth_guard.refresh_live_correction"
    bl_label = "Refresh Live Correction"
    bl_description = "Recompute proximity weights for the current pose/frame"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            body_surface = build_body_surface(body_obj, depsgraph)  # once per frame, shared by every garment
            total_corrected = 0
            global_min = None
            for garment_obj in garments:
                try:
                    stats = correct_current_pose(
                        garment_obj=garment_obj,
                        body_obj=body_obj,
                        depsgraph=depsgraph,
                        body_surface=body_surface,
                        offset_distance=settings.offset_distance,
                        detection_radius=max(settings.offset_distance, settings.detection_radius),
                        correction_strength=settings.correction_strength,
                        max_push_distance=settings.max_push_distance,
                        smooth_iterations=settings.smooth_iterations,
                        smooth_strength=settings.smooth_strength,
                        use_risk_area=settings.use_risk_area,
                        preserve_pinned_areas=settings.preserve_pinned_areas,
                    )
                except Exception as e:  # per-garment boundary, see _report_garment_error
                    _report_garment_error(self, context, garment_obj, "live correction refresh", e)
                    continue
                _warn_unconvertible(
                    self, garment_obj, context.scene.frame_current, stats.unconvertible_verts, "anti-clip push"
                )
                total_corrected += stats.corrected_verts
                if stats.min_nearest_distance is not None:
                    global_min = (
                        stats.min_nearest_distance
                        if global_min is None
                        else min(global_min, stats.min_nearest_distance)
                    )

            min_txt = f"{global_min:.6f} m" if global_min is not None else "n/a"
            self.report({"INFO"}, f"Refreshed live correction (corrected {total_corrected}; min distance {min_txt})")
        return {"FINISHED"}


class CG_OT_create_corrective_shapekey(Operator):
    bl_idname = "cloth_guard.create_corrective_shapekey"
    bl_label = "Create Corrective Shape Key From Current Pose"
    bl_description = "Bake the current corrected result into a new shape key (optionally driven by a bone rotation range)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        garment_obj = context.view_layer.objects.active
        if garment_obj is None or garment_obj.type != "MESH" or garment_obj not in garments:
            self.report({"ERROR"}, "Make a garment mesh from the Garments list the active object to bake a corrective")
            return {"CANCELLED"}

        depsgraph = context.evaluated_depsgraph_get()
        if _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph):
            self.report(
                {"INFO"},
                f"{garment_obj.name}: shape-key corrective is not available with topology-changing modifiers; "
                "use Generate Correction (Current) / Generate Corrections (All Flagged) which switches to helper/modifier mode.",
            )
            return {"CANCELLED"}

        # Ensure live correction exists and is enabled so baking includes it.
        with _temporary_mode_object(context):
            ensure_live_correction_shapekey(garment_obj)
            settings.enable_live_anti_clip = True
            depsgraph = context.evaluated_depsgraph_get()
            body_surface = build_body_surface(settings.body_object, depsgraph)  # once per frame, shared by every garment
            det_before, det_after, used = _run_shape_key_passes(
                context=context,
                settings=settings,
                garment_obj=garment_obj,
                body_obj=settings.body_object,
                depsgraph=depsgraph,
                body_surface=body_surface,
                report_to=self,
            )
            changed_live, max_live = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=CG_SHAPEKEY_LIVE)
            if changed_live <= 0 or max_live <= 1e-12:
                self.report({"WARNING"}, "No correction delta generated for this pose; corrective shape key may be empty")
            else:
                self.report(
                    {"INFO"},
                    f"Live corrective updated: {int(det_before.stats.flagged_clipping)} clipping before, "
                    f"{int(det_after.stats.flagged_clipping)} remaining after {used} pass(es).",
                )

        view_layer = context.view_layer
        prev_active = view_layer.objects.active
        view_layer.objects.active = garment_obj
        garment_obj.select_set(True)

        try:
            # Start from a copy of Basis (Blender adds ".001" if the name is taken), then move the
            # live correction into it. The live key is emptied so the correction is not applied twice.
            new_key = garment_obj.shape_key_add(
                name=(settings.corrective_name.strip() or "CG_Corrective"),
                from_mix=False,
            )
            bake_live_key_into(garment_obj, CG_SHAPEKEY_LIVE, new_key.name)
        except Exception as e:  # shape-key creation on this garment; reported, never silent
            _report_garment_error(self, context, garment_obj, "baking the corrective shape key", e, level="ERROR")
            return {"CANCELLED"}
        finally:
            view_layer.objects.active = prev_active

        new_key.value = 1.0
        changed, max_d = _shapekey_delta_stats(garment_obj=garment_obj, shapekey_name=new_key.name)
        if changed <= 0 or max_d <= 1e-12:
            self.report({"WARNING"}, f"Corrective created but appears empty: {new_key.name}")
        else:
            self.report({"INFO"}, f"{new_key.name} changed {changed} verts; max delta {max_d:.6f} m")

        if settings.driver_enable:
            try:
                arm = settings.driver_armature
                bone = settings.driver_bone.strip()
                if arm is None or bone == "":
                    raise RuntimeError("Set Armature and Bone for driver linking")
                add_shapekey_driver_rotation_range(
                    garment_obj=garment_obj,
                    shapekey_name=new_key.name,
                    armature_obj=arm,
                    bone_name=bone,
                    axis=settings.driver_axis,
                    min_angle_rad=float(settings.driver_min_angle),
                    max_angle_rad=float(settings.driver_max_angle),
                )
            except Exception as e:  # driver API on user data; the key itself was created
                _report_garment_error(self, context, garment_obj, "linking the corrective driver", e)

        log_debug(context, "[Cloth Guard][Corrective]", garment_obj.name, f"frame={context.scene.frame_current}", f"key={new_key.name}", f"delta_verts={changed}", f"max_delta={max_d:.6f}")
        return {"FINISHED"}


class CG_OT_bake_corrections(Operator):
    bl_idname = "cloth_guard.bake_corrections"
    bl_label = "Bake Corrections"
    bl_description = "Bake the current corrected pose into a shape key (MVP)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        frame = context.scene.frame_current
        if not settings.corrective_name or settings.corrective_name.strip() == "":
            settings.corrective_name = f"CG_Bake_{frame}"
        return bpy.ops.cloth_guard.create_corrective_shapekey()
