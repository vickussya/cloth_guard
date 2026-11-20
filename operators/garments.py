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

"""Garment list operators (add / remove / move) and the garment compatibility check."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..core.debug import log_debug
from ._common import (
    _base_vertex_count,
    _eval_vertex_count,
    _likely_topology_modifiers,
    _NON_DESTRUCTIVE_MSG,
    _settings,
    _temporary_mode_object,
    _validate_assigned_meshes,
    _warn_if_unusual_scale,
)


def _compatibility_report_line(
    *, garment_obj: bpy.types.Object, base_count: int, eval_count: int, mods: list[bpy.types.Modifier]
) -> str:
    status = "OK" if base_count == eval_count else "NOT OK"
    if mods:
        mod_txt = "; ".join([f"{m.name}({m.type})" for m in mods])
    else:
        mod_txt = "None detected"
    return f"{garment_obj.name}: base={base_count} eval={eval_count} => {status}; mods: {mod_txt}"


class CG_OT_check_garment_compatibility(Operator):
    bl_idname = "cloth_guard.check_garment_compatibility"
    bl_label = "Check Garment Compatibility"
    bl_description = "Check whether garments can use shape-key correction, or will use helper/modifier mode (topology-changing stacks)"
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
            _warn_if_unusual_scale(self, settings, body_obj, depsgraph)
            bad = 0
            lines = []
            for garment_obj in garments:
                base_count = _base_vertex_count(garment_obj)
                eval_count = _eval_vertex_count(garment_obj, depsgraph)
                mods = _likely_topology_modifiers(garment_obj)
                if base_count != eval_count:
                    bad += 1
                line = _compatibility_report_line(
                    garment_obj=garment_obj,
                    base_count=base_count,
                    eval_count=eval_count,
                    mods=mods,
                )
                lines.append(line)
                log_debug(context, "[Cloth Guard][Compat]", line)

            if bad > 0:
                self.report(
                    {"WARNING"},
                    "Some garments have topology-changing modifiers (topology mismatch). "
                    "Cloth Guard will use a non-destructive helper/modifier workflow for those garments. "
                    "Enable 'Use Cage Mode For Topology-Changing Garments' for best results. See console for details. "
                    + _NON_DESTRUCTIVE_MSG,
                )
            else:
                self.report({"INFO"}, f"All garments OK for corrective shape keys ({len(garments)} checked). See console for details.")
        return {"FINISHED"}


class CG_OT_add_selected_garments(Operator):
    bl_idname = "cloth_guard.add_selected_garments"
    bl_label = "Add Selected Garment"
    bl_description = "Add selected mesh objects to the garment list (ignores non-mesh; avoids duplicates)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        body_obj = settings.body_object

        selected_meshes = [o for o in context.selected_objects if o.type == "MESH"]
        if body_obj is not None:
            selected_meshes = [o for o in selected_meshes if o != body_obj]

        if not selected_meshes:
            self.report({"WARNING"}, "Select one or more garment mesh objects to add")
            return {"CANCELLED"}

        existing = {item.object for item in settings.garments if item.object is not None}
        added = 0
        for obj in selected_meshes:
            if obj in existing:
                continue
            item = settings.garments.add()
            item.object = obj
            item.enabled = True
            existing.add(obj)
            added += 1

        if added == 0:
            self.report({"INFO"}, "No new garments added (already in list)")
            return {"CANCELLED"}

        settings.active_garment_index = max(0, len(settings.garments) - 1)
        self.report({"INFO"}, f"Added {added} garment(s)")
        return {"FINISHED"}


class CG_OT_remove_active_garment(Operator):
    bl_idname = "cloth_guard.remove_active_garment"
    bl_label = "Remove Garment"
    bl_description = "Remove the active garment entry from the garment list"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        idx = int(settings.active_garment_index)
        if idx < 0 or idx >= len(settings.garments):
            self.report({"WARNING"}, "No garment entry selected")
            return {"CANCELLED"}
        settings.garments.remove(idx)
        settings.active_garment_index = max(0, min(idx, len(settings.garments) - 1))
        return {"FINISHED"}


class CG_OT_move_garment(Operator):
    bl_idname = "cloth_guard.move_garment"
    bl_label = "Move Garment"
    bl_description = "Move the active garment up/down in the list"
    bl_options = {"REGISTER", "UNDO"}

    direction: bpy.props.EnumProperty(
        items=(("UP", "Up", ""), ("DOWN", "Down", "")),
        default="UP",
    )

    def execute(self, context):
        settings = _settings(context)
        idx = int(settings.active_garment_index)
        if idx < 0 or idx >= len(settings.garments):
            return {"CANCELLED"}
        if self.direction == "UP":
            new_idx = idx - 1
        else:
            new_idx = idx + 1
        if new_idx < 0 or new_idx >= len(settings.garments):
            return {"CANCELLED"}
        settings.garments.move(idx, new_idx)
        settings.active_garment_index = new_idx
        return {"FINISHED"}
