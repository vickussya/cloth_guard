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

"""Shape preservation operators: store rest shape, analyze drift, preserve current / flagged frames."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..constants import (
    CG_MOD_SHAPE_PRESERVE,
    CG_SHAPEKEY_LIVE_PRESERVE,
    CG_SHAPEKEY_REST,
    CG_VG_SHAPE_DRIFT,
    CG_VG_SHAPE_PRESERVE,
)
from ..core.debug import log_debug
from ..core.mesh_utils import write_weights_to_vertex_group
from ..core.modifiers import ensure_shape_preserve_modifier
from ..core.shape_keys import bake_live_key_into, ensure_live_preserve_shapekey, ensure_rest_shape_shapekey
from ..core.shape_preservation import (
    analyze_shape_drift,
    compute_shape_preserve_mask_weights,
    generate_shape_preservation,
    store_rest_shape,
)
from ._common import (
    _base_vertex_count,
    _eval_vertex_count,
    _likely_topology_modifiers,
    _report_garment_error,
    _settings,
    _temporary_mode_object,
    _topology_mismatch,
    _validate_assigned_meshes,
)
from ._keyframes import _keyframe_modifier_factor, _keyframe_shapekey_value
from ._passes import _warn_unconvertible


def _is_modifier_bound(mod) -> bool:
    if mod is None:
        return False
    if bool(getattr(mod, "is_bind", False)):
        return True
    if bool(getattr(mod, "is_bound", False)):
        return True
    rs = getattr(mod, "rest_source", None)
    # If rest_source is set to BIND, treat it as likely bound.
    return bool(rs == "BIND")


def _set_rest_status_props(
    garment_obj: bpy.types.Object,
    *,
    mode: str,
    base_count: int,
    eval_count: int,
    success: bool,
) -> None:
    """
    Persist rest-shape storage status on the object so it survives save/load and UI refresh.
    """
    try:
        garment_obj["cg_rest_mode"] = str(mode)
        garment_obj["cg_rest_base_verts"] = int(base_count)
        garment_obj["cg_rest_eval_verts"] = int(eval_count)
        garment_obj["cg_rest_success"] = int(1 if success else 0)
    except (TypeError, AttributeError) as e:
        # Linked / library-override objects can refuse custom properties. This is only the
        # "Rest: Stored" UI hint, so the rest-shape operation itself is not failed for it.
        log_debug(bpy.context, "[Cloth Guard][Rest] could not save status on", garment_obj.name, e)


def _bind_corrective_smooth(context, *, obj: bpy.types.Object, modifier_name: str) -> bool:
    """
    Bind a Corrective Smooth modifier using a reliable override so it actually persists.
    """
    override = {
        "object": obj,
        "active_object": obj,
        "selected_objects": [obj],
        "selected_editable_objects": [obj],
    }
    try:
        # context.temp_override() exists since Blender 3.2; older versions take an override dict.
        if bpy.app.version >= (3, 2, 0):
            with context.temp_override(**override):
                res = bpy.ops.object.correctivesmooth_bind(modifier=modifier_name)
        else:
            legacy = context.copy()
            legacy.update(override)
            res = bpy.ops.object.correctivesmooth_bind(legacy, modifier=modifier_name)
        return bool("FINISHED" in res)
    except RuntimeError as e:  # bpy.ops raises RuntimeError when poll fails or the operator errors
        log_debug(context, "[Cloth Guard][RestBind] bind failed:", obj.name, modifier_name, e)
        return False


class CG_OT_store_rest_shape(Operator):
    bl_idname = "cloth_guard.store_rest_shape"
    bl_label = "Store Rest Shape"
    bl_description = "Stores the clean garment form used as reference for shape preservation"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            stored = 0
            for garment_obj in garments:
                try:
                    base_count = _base_vertex_count(garment_obj)
                    eval_count = _eval_vertex_count(garment_obj, depsgraph)
                    topo_mods = _likely_topology_modifiers(garment_obj)
                    mismatch = base_count != eval_count

                    log_debug(
                        context,
                        "[Cloth Guard][Rest][Debug]",
                        garment_obj.name,
                        f"base={base_count}",
                        f"eval={eval_count}",
                        f"topo_mods={len(topo_mods)}",
                    )

                    if mismatch:
                        # Topology-changing stack: store the VISUAL reference by binding a post-stack Corrective Smooth.
                        mode = "EvaluatedMode"
                        weights = compute_shape_preserve_mask_weights(
                            garment_obj,
                            protect_groups=bool(settings.protect_preserve_groups),
                        )
                        write_weights_to_vertex_group(garment_obj, CG_VG_SHAPE_PRESERVE, weights)

                        mod = ensure_shape_preserve_modifier(
                            garment_obj,
                            iterations=int(settings.wrinkle_smoothing_iterations),
                            factor=0.0,
                        )
                        if hasattr(mod, "vertex_group"):
                            mod.vertex_group = CG_VG_SHAPE_PRESERVE

                        # Bind uses the current evaluated visual shape (including Subdivision/Geo Nodes).
                        ok = _bind_corrective_smooth(context, obj=garment_obj, modifier_name=mod.name)
                        ok = ok or _is_modifier_bound(mod)

                        _set_rest_status_props(
                            garment_obj,
                            mode=mode,
                            base_count=base_count,
                            eval_count=eval_count,
                            success=ok,
                        )

                        if not ok:
                            self.report(
                                {"ERROR"},
                                f"{garment_obj.name}: failed to store evaluated rest shape (modifier bind). "
                                "Make sure the garment is selectable and try again. See console for details.",
                            )
                            log_debug(context, "[Cloth Guard][RestBind] FAILED", garment_obj.name, "modifier", mod.name)
                            continue

                        stored += 1
                        self.report({"INFO"}, f"Stored evaluated rest shape for {garment_obj.name} ({eval_count} verts) using {mode}.")
                        log_debug(context, "[Cloth Guard][RestBind] OK", garment_obj.name, "modifier", mod.name)
                        continue

                    # Topology-stable: store an exact per-vertex rest shape key.
                    mode = "ShapeKeyMode"
                    ensure_rest_shape_shapekey(garment_obj)
                    written = store_rest_shape(garment_obj=garment_obj, depsgraph=depsgraph)
                    _set_rest_status_props(
                        garment_obj,
                        mode=mode,
                        base_count=base_count,
                        eval_count=eval_count,
                        success=True,
                    )
                    stored += 1
                    self.report({"INFO"}, f"Stored rest shape for {garment_obj.name}: {written} vertices using {mode}.")
                    log_debug(context, "[Cloth Guard][Rest] OK", garment_obj.name, f"written={written}", mode)
                except Exception as e:  # per-garment boundary, see _report_garment_error
                    _set_rest_status_props(
                        garment_obj,
                        mode="ERROR",
                        base_count=_base_vertex_count(garment_obj),
                        eval_count=0,
                        success=False,
                    )
                    _report_garment_error(self, context, garment_obj, "storing the rest shape", e)

            self.report({"INFO"}, f"Stored rest shape for {stored} garment(s) (non-destructive)")
        return {"FINISHED"}


class CG_OT_analyze_shape_drift(Operator):
    bl_idname = "cloth_guard.analyze_shape_drift"
    bl_label = "Analyze Shape Drift"
    bl_description = "Checks how much the garment shape has changed from the stored reference"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            analyzed = 0
            for garment_obj in garments:
                try:
                    mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)
                    if mismatch:
                        self.report(
                            {"INFO"},
                            f"{garment_obj.name}: drift analysis is not available in visual modifier mode. "
                            "Use Preserve Shape (Current) to preview, or use the mask group to protect areas.",
                        )
                        continue

                    stats, weights = analyze_shape_drift(
                        garment_obj=garment_obj,
                        depsgraph=depsgraph,
                        drift_threshold=settings.drift_threshold,
                        protect_borders=settings.protect_borders,
                    )
                    write_weights_to_vertex_group(garment_obj, CG_VG_SHAPE_DRIFT, weights)
                    analyzed += 1
                    avg_txt = f"{stats.avg_flagged_drift:.6f} m" if stats.avg_flagged_drift is not None else "n/a"
                    self.report(
                        {"INFO"},
                        f"{garment_obj.name}: drift flagged {stats.flagged_verts}/{stats.checked_verts}; max {stats.max_drift_distance:.6f} m; avg {avg_txt}",
                    )
                    log_debug(context, "[Cloth Guard][Drift]", garment_obj.name, f"flagged={stats.flagged_verts}", f"max={stats.max_drift_distance:.6f}")
                except Exception as e:  # per-garment boundary, see _report_garment_error
                    _report_garment_error(self, context, garment_obj, "drift analysis", e)

            if analyzed == 0:
                self.report({"ERROR"}, "No garments analyzed. Store Rest Shape first.")
                return {"CANCELLED"}
        return {"FINISHED"}


class CG_OT_generate_shape_preservation_current(Operator):
    bl_idname = "cloth_guard.generate_shape_preservation_current"
    bl_label = "Preserve Shape (Current Frame)"
    bl_description = "Creates a non-destructive correction to reduce unwanted deformation on the current frame"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()
            done = 0
            missing_rest = 0
            zero_delta = 0
            failed = 0
            for garment_obj in garments:
                try:
                    mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)

                    if mismatch:
                        mod = garment_obj.modifiers.get(CG_MOD_SHAPE_PRESERVE)
                        is_bound = bool(getattr(mod, "is_bind", False)) if mod is not None else False
                        if mod is None or not is_bound:
                            missing_rest += 1
                            self.report(
                                {"WARNING"},
                                f"{garment_obj.name}: Rest Shape is missing for visual modifier mode. "
                                "Go to a clean frame and click Store Rest Shape.",
                            )
                            continue
                        if hasattr(mod, "factor"):
                            mod.factor = float(settings.shape_strength)
                        done += 1
                        self.report({"INFO"}, f"{garment_obj.name}: preserve active (visual modifier mode)")
                        continue

                    keys = garment_obj.data.shape_keys
                    if keys is None or keys.key_blocks.get(CG_SHAPEKEY_REST) is None:
                        missing_rest += 1
                        self.report({"WARNING"}, f"{garment_obj.name}: Rest Shape is missing. Click Store Rest Shape on a clean frame.")
                        continue

                    ensure_live_preserve_shapekey(garment_obj)
                    changed, max_d, unconvertible = generate_shape_preservation(
                        garment_obj=garment_obj,
                        depsgraph=depsgraph,
                        strength=settings.shape_strength,
                        smoothing_iterations=settings.wrinkle_smoothing_iterations,
                        smoothing_strength=settings.wrinkle_smoothing_strength,
                        volume_preservation=settings.volume_preservation,
                        silhouette_preservation=settings.silhouette_preservation,
                        drift_threshold=settings.drift_threshold,
                        protect_borders=settings.protect_borders,
                        protect_groups=settings.protect_preserve_groups,
                    )
                    _warn_unconvertible(self, garment_obj, context.scene.frame_current, unconvertible, "shape preservation")
                    if changed <= 0:
                        zero_delta += 1
                        self.report(
                            {"INFO"},
                            f"{garment_obj.name}: no shape preservation change generated. "
                            "Try increasing Shape Strength or Wrinkle Smooth Strength.",
                        )
                        continue
                    done += 1
                    self.report({"INFO"}, f"{garment_obj.name}: preserve changed {changed} verts; max delta {max_d:.6f} m")
                    log_debug(context, "[Cloth Guard][Preserve]", garment_obj.name, f"changed={changed}", f"max_delta={max_d:.6f}")
                except Exception as e:  # per-garment boundary, see _report_garment_error
                    failed += 1
                    _report_garment_error(self, context, garment_obj, "shape preservation", e)

            if done == 0:
                if missing_rest == len(garments):
                    self.report({"ERROR"}, "Rest Shape is missing for all garments. Click Store Rest Shape on a clean frame first.")
                elif missing_rest > 0:
                    self.report({"ERROR"}, f"Rest Shape is missing for {missing_rest} garment(s). See warnings above.")
                elif zero_delta > 0 and failed == 0:
                    self.report({"ERROR"}, "No preservation change was generated. Try stronger Shape Strength / Wrinkle Smooth Strength.")
                else:
                    self.report({"ERROR"}, "No preservation corrections generated. Check the console for details.")
                return {"CANCELLED"}

            settings.enable_live_anti_clip = True
        return {"FINISHED"}


class CG_OT_generate_shape_preservation_flagged(Operator):
    bl_idname = "cloth_guard.generate_shape_preservation_flagged"
    bl_label = "Preserve Shape (All Flagged Frames)"
    bl_description = "Batch: creates non-destructive shape preservation corrections for all flagged frames"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        _, garments = validated

        if len(settings.problem_frames) == 0:
            self.report({"ERROR"}, "No problem frames stored. Run Scan Animation first.")
            return {"CANCELLED"}

        scene = context.scene
        prev_frame = int(scene.frame_current)
        frames = sorted(set(int(it.frame) for it in settings.problem_frames))

        view_layer = context.view_layer
        prev_active = view_layer.objects.active

        baked = 0
        wm = context.window_manager
        wm.progress_begin(0, len(frames))  # progress counter so Blender does not look frozen
        try:
            with _temporary_mode_object(context):
                for frame_index, frame in enumerate(frames):
                    wm.progress_update(frame_index)
                    scene.frame_set(frame)
                    depsgraph = context.evaluated_depsgraph_get()
                    for garment_obj in garments:
                        try:
                            mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)
                            if mismatch:
                                mod = garment_obj.modifiers.get(CG_MOD_SHAPE_PRESERVE)
                                is_bound = bool(getattr(mod, "is_bind", False)) if mod is not None else False
                                if mod is None or not is_bound:
                                    self.report(
                                        {"WARNING"},
                                        f"{garment_obj.name}: missing visual rest bind. Go to a clean frame and click Store Rest Shape.",
                                    )
                                    continue
                                _keyframe_modifier_factor(
                                    garment_obj=garment_obj,
                                    mod_name=CG_MOD_SHAPE_PRESERVE,
                                    frame=frame - 1,
                                    value=0.0,
                                )
                                _keyframe_modifier_factor(
                                    garment_obj=garment_obj,
                                    mod_name=CG_MOD_SHAPE_PRESERVE,
                                    frame=frame,
                                    value=float(settings.shape_strength),
                                )
                                _keyframe_modifier_factor(
                                    garment_obj=garment_obj,
                                    mod_name=CG_MOD_SHAPE_PRESERVE,
                                    frame=frame + 1,
                                    value=0.0,
                                )
                                baked += 1
                                continue

                            ensure_live_preserve_shapekey(garment_obj)
                            changed, max_d, unconvertible = generate_shape_preservation(
                                garment_obj=garment_obj,
                                depsgraph=depsgraph,
                                strength=settings.shape_strength,
                                smoothing_iterations=settings.wrinkle_smoothing_iterations,
                                smoothing_strength=settings.wrinkle_smoothing_strength,
                                volume_preservation=settings.volume_preservation,
                                silhouette_preservation=settings.silhouette_preservation,
                                drift_threshold=settings.drift_threshold,
                                protect_borders=settings.protect_borders,
                                protect_groups=settings.protect_preserve_groups,
                            )
                            _warn_unconvertible(self, garment_obj, context.scene.frame_current, unconvertible, "shape preservation")
                            if changed <= 0:
                                continue

                            view_layer.objects.active = garment_obj
                            key_name = f"CG_Preserve_{frame:04d}"
                            # Move only the CG_LivePreserve offset into the frame key and empty the live key,
                            # so the preservation is applied once.
                            bake_live_key_into(garment_obj, CG_SHAPEKEY_LIVE_PRESERVE, key_name)

                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame - 1, value=0.0)
                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame, value=1.0)
                            _keyframe_shapekey_value(garment_obj=garment_obj, key_name=key_name, frame=frame + 1, value=0.0)
                            baked += 1
                            log_debug(context, "[Cloth Guard][PreserveBake]", garment_obj.name, f"frame={frame}", f"key={key_name}", f"max_delta={max_d:.6f}")
                        except Exception as e:  # per-garment boundary, see _report_garment_error
                            _report_garment_error(self, context, garment_obj, "preserve bake", e, frame=frame)
                settings.enable_live_anti_clip = True
        finally:
            wm.progress_end()
            scene.frame_set(prev_frame)
            view_layer.objects.active = prev_active

        self.report({"INFO"}, f"Preserve bake complete: {len(frames)} frame(s), {baked} baked item(s)")
        return {"FINISHED"}
