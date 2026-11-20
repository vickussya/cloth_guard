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

"""Multi-pass correction with "never keep a worse correction" verification, and baking the live key."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..constants import (
    ATTEMPTS_PER_PASS,
    CG_MOD_ANTICLIP,
    CG_SHAPEKEY_LIVE,
    HELPER_VERIFY_ATTEMPTS,
    MAX_CORRECTION_PASSES,
    RETRY_STRENGTH_FACTOR,
)
from ..core.body import BodySurface, build_body_surface
from ..core.correction import correct_current_pose
from ..core.debug import log_debug
from ..core.modifiers import anticlip_strength_base, set_gn_modifier_input
from ..core.shape_keys import bake_live_key_into, ensure_live_correction_shapekey
from ._common import _run_detection_and_weights


def _get_or_create_anticlip_mod(garment_obj: bpy.types.Object) -> bpy.types.Modifier | None:
    mod = garment_obj.modifiers.get(CG_MOD_ANTICLIP)
    if mod is None:
        return None
    return mod


def _warn_unconvertible(report_to: Operator | None, garment_obj: bpy.types.Object, frame: int, count: int, what: str) -> None:
    """
    Tell the artist when some pushes could not be converted back through the rig (degenerate deformation).

    Those vertices keep the push as measured on the posed garment, which can point the wrong way once
    the Armature re-poses it, so the artist must check that frame.
    """
    if report_to is None or count <= 0:
        return
    report_to.report(
        {"WARNING"},
        f"{garment_obj.name}, frame {frame}: {count} vertices could not be converted back through the rig, "
        f"so their {what} may point the wrong way. Next: go to frame {frame} and check them. If they look wrong, "
        "look for a bone scaled to 0 or a modifier between the shape keys and the Armature that flattens the "
        "garment (e.g. Shrinkwrap), fix it and run the button again.",
    )


def _run_shape_key_passes(
    *,
    context,
    settings,
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    depsgraph,
    body_surface: BodySurface | None = None,
    report_to: Operator | None = None,
) -> tuple[object, object, int]:
    """
    Run multi-pass shape-key correction.

    report_to: operator that receives the "could not convert through the rig" warning.
    Returns: (det_before, det_after, passes_used)
    """
    if body_surface is None:
        body_surface = build_body_surface(body_obj, depsgraph)
    passes = int(getattr(settings, "correction_passes", 1))
    passes = max(1, min(MAX_CORRECTION_PASSES, passes))

    target_offset = max(0.0, float(settings.offset_distance + settings.safety_margin))
    det_before, contact_affected, clipping_affected = _run_detection_and_weights(
        context=context,
        settings=settings,
        garment_obj=garment_obj,
        body_obj=body_obj,
        depsgraph=depsgraph,
        body_surface=body_surface,
        offset_distance=target_offset,
        detection_radius=max(target_offset, settings.detection_radius),
    )

    # If nothing is clipping, we can skip heavy work.
    if int(det_before.stats.flagged_clipping) <= 0:
        return det_before, det_before, 0

    strength = float(settings.correction_strength) * float(settings.push_multiplier)
    strength = max(0.0, min(1.0, strength))

    mode = str(getattr(settings, "anticlip_mode", "SAFE_PUSHOUT"))
    smooth_it = int(settings.smooth_iterations) if mode == "SMART" else 0
    smooth_st = float(settings.smooth_strength) if mode == "SMART" else 0.0

    def _is_worse(before_det, after_det) -> bool:
        b_c = int(before_det.stats.flagged_clipping)
        a_c = int(after_det.stats.flagged_clipping)
        if a_c > b_c:
            return True
        b_p = before_det.stats.max_penetration
        a_p = after_det.stats.max_penetration
        if b_p is not None and a_p is not None and a_p > b_p + 1e-8:
            return True
        return False

    def _snapshot_keyblock(kb) -> tuple[list, float]:
        return [p.co.copy() for p in kb.data], float(kb.value)

    def _restore_keyblock(kb, snap: tuple[list, float]) -> None:
        coords, value = snap
        if len(kb.data) != len(coords):
            return
        for i in range(len(coords)):
            kb.data[i].co = coords[i]
        kb.value = value

    live_kb = ensure_live_correction_shapekey(garment_obj)
    # Track best accepted detection so we never keep a worse result.
    det_best = det_before
    # Pushed vertices the rig deformation could not be undone for (largest count over accepted passes).
    unconvertible = 0

    try:
        for p in range(passes):
            # Verification-based correction: never keep a correction that makes clipping worse.
            attempt_strength = float(strength)
            accepted = False
            det_after = det_best
            stats = None

            snap = _snapshot_keyblock(live_kb)
            for attempt in range(ATTEMPTS_PER_PASS):
                stats = correct_current_pose(
                    garment_obj=garment_obj,
                    body_obj=body_obj,
                    depsgraph=depsgraph,
                    body_surface=body_surface,
                    offset_distance=target_offset,
                    detection_radius=max(target_offset, settings.detection_radius),
                    correction_strength=attempt_strength,
                    max_push_distance=settings.max_push_distance,
                    smooth_iterations=smooth_it,
                    smooth_strength=smooth_st,
                    use_risk_area=settings.use_risk_area,
                    preserve_pinned_areas=settings.preserve_pinned_areas,
                )

                context.view_layer.update()
                depsgraph = context.evaluated_depsgraph_get()

                det_after, _, _ = _run_detection_and_weights(
                    context=context,
                    settings=settings,
                    garment_obj=garment_obj,
                    body_obj=body_obj,
                    depsgraph=depsgraph,
                    body_surface=body_surface,
                    offset_distance=target_offset,
                    detection_radius=max(target_offset, settings.detection_radius),
                )

                if _is_worse(det_best, det_after):
                    _restore_keyblock(live_kb, snap)
                    context.view_layer.update()
                    depsgraph = context.evaluated_depsgraph_get()
                    attempt_strength *= RETRY_STRENGTH_FACTOR
                    continue

                accepted = True
                det_best = det_after
                unconvertible = max(unconvertible, int(stats.unconvertible_verts))
                break

            log_debug(
                context,
                "[Cloth Guard][Pass]",
                garment_obj.name,
                f"pass={p+1}/{passes}",
                f"before={int(det_before.stats.flagged_clipping)}",
                f"after={int(det_after.stats.flagged_clipping)}",
                f"accepted={'YES' if accepted else 'NO'}",
                (f"changed={getattr(stats, 'changed_verts', 0)}" if stats is not None else "changed=0"),
                (f"max_delta={getattr(stats, 'max_delta_distance', 0.0):.6f} m" if stats is not None else "max_delta=0.000000 m"),
                (f"pen_before={float(det_before.stats.max_penetration or 0.0):.6f} m" if hasattr(det_before.stats, "max_penetration") else ""),
                (f"pen_after={float(det_after.stats.max_penetration or 0.0):.6f} m" if hasattr(det_after.stats, "max_penetration") else ""),
            )

            if not accepted:
                # Rejected: stop early; we refuse to keep a worse correction.
                return det_before, det_best, p
            if int(det_best.stats.flagged_clipping) <= 0:
                return det_before, det_best, p + 1

        return det_before, det_best, passes
    finally:
        _warn_unconvertible(report_to, garment_obj, context.scene.frame_current, unconvertible, "anti-clip push")


def _set_anticlip_helper_factor(*, garment_obj: bpy.types.Object, value: float) -> None:
    mod = garment_obj.modifiers.get(CG_MOD_ANTICLIP)
    if mod is None:
        return
    if getattr(mod, "type", "") == "NODES" and getattr(mod, "node_group", None) is not None:
        set_gn_modifier_input(mod, "Factor", float(value))
        return
    if hasattr(mod, "strength"):
        base = anticlip_strength_base(garment_obj, mod, 1.0)
        mod.strength = float(value) * base


def _verify_helper_correction(
    *,
    context,
    settings,
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    depsgraph,
    body_surface: BodySurface | None = None,
    det_before,
    target_offset: float,
) -> tuple[float, object]:
    """
    Apply helper correction (GN/Displace) and verify it does not worsen body clipping.
    Returns (accepted_factor, det_after_best).
    """
    if body_surface is None:
        body_surface = build_body_surface(body_obj, depsgraph)
    factor = 1.0
    det_best = det_before

    def _is_worse(before_det, after_det) -> bool:
        if int(after_det.stats.flagged_clipping) > int(before_det.stats.flagged_clipping):
            return True
        b_p = getattr(before_det.stats, "max_penetration", None)
        a_p = getattr(after_det.stats, "max_penetration", None)
        if b_p is not None and a_p is not None and float(a_p) > float(b_p) + 1e-8:
            return True
        return False

    for _ in range(HELPER_VERIFY_ATTEMPTS):
        _set_anticlip_helper_factor(garment_obj=garment_obj, value=factor)
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        det_after, _, _ = _run_detection_and_weights(
            context=context,
            settings=settings,
            garment_obj=garment_obj,
            body_obj=body_obj,
            depsgraph=depsgraph,
            body_surface=body_surface,
            offset_distance=target_offset,
            detection_radius=max(target_offset, settings.detection_radius),
            # Measure WITH the correction (cage mode would switch CG_AntiClip off too), and do not
            # rewrite CG_Clipping: the modifier reads it, so fixed vertices would lose their push.
            keep_enabled=(CG_MOD_ANTICLIP,),
            write_weights=False,
        )
        if _is_worse(det_best, det_after):
            factor *= RETRY_STRENGTH_FACTOR
            continue
        det_best = det_after
        return float(factor), det_best

    # Reject: disable helper correction.
    _set_anticlip_helper_factor(garment_obj=garment_obj, value=0.0)
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    det_after, _, _ = _run_detection_and_weights(
        context=context,
        settings=settings,
        garment_obj=garment_obj,
        body_obj=body_obj,
        depsgraph=depsgraph,
        body_surface=body_surface,
        offset_distance=target_offset,
        detection_radius=max(target_offset, settings.detection_radius),
        keep_enabled=(CG_MOD_ANTICLIP,),
        write_weights=False,
    )
    return 0.0, det_after


def _update_correction_weights(context, *, report_to: Operator | None = None) -> int:
    return 0


def _bake_live_correction_to_key(garment_obj: bpy.types.Object, name: str) -> None:
    """
    Move the CG_LiveCorrection offset into a named per-frame shape key and empty the live key.

    (It used to copy the whole visible mix and leave the live key on, so the correction was
    applied twice, and any active user shape key was baked in a second time.)
    """
    if garment_obj.type != "MESH":
        raise RuntimeError("Garment is not a mesh")
    ensure_live_correction_shapekey(garment_obj)
    bake_live_key_into(garment_obj, CG_SHAPEKEY_LIVE, name)
