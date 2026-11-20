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

"""Keyframe helpers for per-frame corrections (shape-key values and modifier strengths, CONSTANT interpolation)."""

from __future__ import annotations

import bpy

from ..core.modifiers import anticlip_strength_base, gn_modifier_input, set_gn_modifier_input


def _find_fcurve(anim_data, data_path: str):
    """
    Return the F-Curve animating data_path in anim_data's action, or None.

    Blender 4.4 moved F-Curves into per-slot "channelbags" (layered actions) and Blender 5.0
    removed the old Action.fcurves shortcut, so newer versions must go through the slot.
    """
    if anim_data is None or anim_data.action is None:
        return None
    if bpy.app.version >= (4, 4, 0):
        from bpy_extras import anim_utils

        channelbag = anim_utils.action_get_channelbag_for_slot(anim_data.action, anim_data.action_slot)
        return channelbag.fcurves.find(data_path) if channelbag is not None else None
    return anim_data.action.fcurves.find(data_path=data_path)


def _keyframe_modifier_strength(*, garment_obj: bpy.types.Object, mod_name: str, frame: int, value: float) -> None:
    mod = garment_obj.modifiers.get(mod_name)
    if mod is None:
        return

    # Geometry Nodes: keyframe the "Factor" input (0..1) when available.
    if getattr(mod, "type", "") == "NODES" and getattr(mod, "node_group", None) is not None:
        ref = gn_modifier_input(mod, "Factor")
        if ref is None:
            return
        owner, prop_path = ref
        set_gn_modifier_input(mod, "Factor", float(value))
        owner.keyframe_insert(data_path=prop_path, frame=int(frame))
        ad = garment_obj.animation_data
        if ad is None or ad.action is None:
            return
        data_path = owner.path_from_id(prop_path)
        fc = _find_fcurve(ad, data_path)
        if fc is None:
            return
        for kp in fc.keyframe_points:
            if int(round(kp.co.x)) == int(frame):
                kp.interpolation = "CONSTANT"
        return

    # Displace / others: keyframe strength scaled by base.
    if not hasattr(mod, "strength"):
        return
    base = anticlip_strength_base(garment_obj, mod, 1.0)
    mod.strength = float(value) * float(base)
    mod.keyframe_insert(data_path="strength", frame=int(frame))

    ad = garment_obj.animation_data
    if ad is None or ad.action is None:
        return
    data_path = f'modifiers["{mod.name}"].strength'
    fc = _find_fcurve(ad, data_path)
    if fc is None:
        return
    for kp in fc.keyframe_points:
        if int(round(kp.co.x)) == int(frame):
            kp.interpolation = "CONSTANT"


def _keyframe_shapekey_value(*, garment_obj: bpy.types.Object, key_name: str, frame: int, value: float) -> None:
    if garment_obj.data.shape_keys is None:
        return
    kb = garment_obj.data.shape_keys.key_blocks.get(key_name)
    if kb is None:
        return
    kb.value = float(value)
    kb.keyframe_insert("value", frame=int(frame))
    ad = garment_obj.data.shape_keys.animation_data
    if ad is None or ad.action is None:
        return
    data_path = f'key_blocks["{kb.name}"].value'
    fc = _find_fcurve(ad, data_path)
    if fc is None:
        return
    for kp in fc.keyframe_points:
        if int(round(kp.co.x)) == int(frame):
            kp.interpolation = "CONSTANT"


def _keyframe_modifier_factor(*, garment_obj: bpy.types.Object, mod_name: str, frame: int, value: float) -> None:
    mod = garment_obj.modifiers.get(mod_name)
    if mod is None or not hasattr(mod, "factor"):
        return
    mod.factor = float(value)
    mod.keyframe_insert(data_path="factor", frame=int(frame))

    ad = garment_obj.animation_data
    if ad is None or ad.action is None:
        return
    data_path = f'modifiers["{mod.name}"].factor'
    fc = _find_fcurve(ad, data_path)
    if fc is None:
        return
    for kp in fc.keyframe_points:
        if int(round(kp.co.x)) == int(frame):
            kp.interpolation = "CONSTANT"
