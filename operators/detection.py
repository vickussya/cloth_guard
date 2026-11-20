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

"""Detect and select clipping / self-clipping operators."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..constants import CG_VG_CLIPPING, CG_VG_SELF_CLIPPING
from ..core.body import build_body_surface
from ..core.debug import log_debug
from ..core.detection import detect_self_clipping
from ..core.mesh_utils import write_weights_to_vertex_group
from ._common import (
    _likely_topology_modifiers,
    _report_garment_error,
    _run_detection_and_weights,
    _settings,
    _temporarily_disable_modifiers,
    _temporary_mode_object,
    _topology_mismatch,
    _validate_assigned_meshes,
    _validate_garments_only,
)


class CG_OT_detect_clipping(Operator):
    bl_idname = "cloth_guard.detect_clipping"
    bl_label = "Detect Clipping"
    bl_description = "Finds garment vertices that are likely intersecting or too close to the body"
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
            global_min = None

            for garment_obj in garments:
                try:
                    res, contact_affected, clipping_affected = _run_detection_and_weights(
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
                    _report_garment_error(self, context, garment_obj, "clipping detection", e)
                    continue

                s = res.stats
                total_checked += s.checked_verts
                total_candidates += s.candidates_within_radius
                total_contact += contact_affected
                total_clipping += clipping_affected
                if s.min_nearest_distance is not None:
                    global_min = (
                        s.min_nearest_distance
                        if global_min is None
                        else min(global_min, s.min_nearest_distance)
                    )

                min_txt = f"{s.min_nearest_distance:.6f} m" if s.min_nearest_distance is not None else "n/a"
                log_debug(
                    context,
                    "[Cloth Guard][Detect]",
                    garment_obj.name,
                    f"checked={s.checked_verts}",
                    f"candidates={s.candidates_within_radius}",
                    f"contact={contact_affected}",
                    f"clipping={clipping_affected}",
                    f"min={min_txt}",
                )

        global_min_txt = f"{global_min:.6f} m" if global_min is not None else "n/a"
        msg = (
            f"Processed {len(garments)} garment(s); checked {total_checked} verts; "
            f"{total_candidates} within radius; {total_contact} contact; {total_clipping} clipping; min distance {global_min_txt}"
        )
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class CG_OT_select_clipping_vertices(Operator):
    bl_idname = "cloth_guard.select_clipping_vertices"
    bl_label = "Select Clipping Vertices"
    bl_description = "Select vertices flagged in the CG_Clipping vertex group (Edit Mode)"
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
            self.report({"ERROR"}, "Make a garment mesh from the Garments list the active object, then run Select")
            return {"CANCELLED"}

        vg = garment_obj.vertex_groups.get(CG_VG_CLIPPING)
        if vg is None:
            self.report({"ERROR"}, f"Missing vertex group: {CG_VG_CLIPPING}")
            return {"CANCELLED"}

        view_layer = context.view_layer
        prev_active = view_layer.objects.active
        view_layer.objects.active = garment_obj
        garment_obj.select_set(True)
        try:
            if garment_obj.mode != "EDIT":
                bpy.ops.object.mode_set(mode="EDIT")

            import bmesh

            bm = bmesh.from_edit_mesh(garment_obj.data)
            mesh = garment_obj.data
            group_index = vg.index
            for v in bm.verts:
                v.select_set(False)
                for g in mesh.vertices[v.index].groups:
                    if g.group == group_index and g.weight > 0.0:
                        v.select_set(True)
                        break
            bmesh.update_edit_mesh(garment_obj.data, loop_triangles=False, destructive=False)
        finally:
            view_layer.objects.active = prev_active

        self.report({"INFO"}, f"Selected {CG_VG_CLIPPING} vertices")
        return {"FINISHED"}


class CG_OT_detect_self_clipping(Operator):
    bl_idname = "cloth_guard.detect_self_clipping"
    bl_label = "Detect Self-Clipping"
    bl_description = "Experimental: finds garment vertices likely intersecting other parts of the same garment"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        garments = _validate_garments_only(settings)
        if not garments:
            self.report({"ERROR"}, "Add one or more garment mesh objects to the Garments list first")
            return {"CANCELLED"}

        with _temporary_mode_object(context):
            depsgraph = context.evaluated_depsgraph_get()

            total_checked = 0
            total_flagged = 0
            global_min = None

            for garment_obj in garments:
                # Use cage mode when topology changes, so weights can be written to the base mesh.
                mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)
                mods = _likely_topology_modifiers(garment_obj) if (mismatch or settings.ignore_topology_modifiers) else []

                try:
                    with _temporarily_disable_modifiers(garment_obj, mods):
                        if mods:
                            context.view_layer.update()
                            depsgraph = context.evaluated_depsgraph_get()
                        stats, weights = detect_self_clipping(
                            garment_obj=garment_obj,
                            depsgraph=depsgraph,
                            radius=float(settings.selfclip_radius),
                            ignore_neighbor_rings=int(settings.selfclip_ignore_rings),
                        )
                    affected = write_weights_to_vertex_group(garment_obj, CG_VG_SELF_CLIPPING, weights)

                    total_checked += int(stats.checked_verts)
                    total_flagged += int(affected)
                    if stats.min_distance is not None:
                        global_min = stats.min_distance if global_min is None else min(global_min, stats.min_distance)

                    min_txt = f"{stats.min_distance:.6f} m" if stats.min_distance is not None else "n/a"
                    log_debug(
                        context,
                        "[Cloth Guard][SelfClip]",
                        garment_obj.name,
                        f"checked={int(stats.checked_verts)}",
                        f"flagged={int(affected)}",
                        f"min={min_txt}",
                        ("cage=ON" if mods else "cage=OFF"),
                    )
                except Exception as e:  # per-garment boundary, see _report_garment_error
                    _report_garment_error(self, context, garment_obj, "self-clipping detection", e)

        global_min_txt = f"{global_min:.6f} m" if global_min is not None else "n/a"
        self.report(
            {"INFO"},
            f"Self-clipping: processed {len(garments)} garment(s); checked {total_checked} verts; flagged {total_flagged}; min distance {global_min_txt}",
        )
        return {"FINISHED"}


class CG_OT_select_self_clipping_vertices(Operator):
    bl_idname = "cloth_guard.select_self_clipping_vertices"
    bl_label = "Select Self-Clipping Vertices"
    bl_description = "Select vertices flagged in the CG_SelfClipping vertex group (Edit Mode)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        garments = _validate_garments_only(settings)
        if not garments:
            self.report({"ERROR"}, "Add garment mesh objects to the Garments list first")
            return {"CANCELLED"}

        garment_obj = context.view_layer.objects.active
        if garment_obj is None or garment_obj.type != "MESH" or garment_obj not in garments:
            self.report({"ERROR"}, "Make a garment mesh from the Garments list the active object, then run Select")
            return {"CANCELLED"}

        vg = garment_obj.vertex_groups.get(CG_VG_SELF_CLIPPING)
        if vg is None:
            self.report({"ERROR"}, f"Missing vertex group: {CG_VG_SELF_CLIPPING}")
            return {"CANCELLED"}

        view_layer = context.view_layer
        prev_active = view_layer.objects.active
        view_layer.objects.active = garment_obj
        garment_obj.select_set(True)
        try:
            if garment_obj.mode != "EDIT":
                bpy.ops.object.mode_set(mode="EDIT")

            import bmesh

            bm = bmesh.from_edit_mesh(garment_obj.data)
            mesh = garment_obj.data
            group_index = vg.index
            for v in bm.verts:
                v.select_set(False)
                for g in mesh.vertices[v.index].groups:
                    if g.group == group_index and g.weight > 0.0:
                        v.select_set(True)
                        break
            bmesh.update_edit_mesh(garment_obj.data, loop_triangles=False, destructive=False)
        finally:
            view_layer.objects.active = prev_active

        self.report({"INFO"}, f"Selected {CG_VG_SELF_CLIPPING} vertices")
        return {"FINISHED"}
