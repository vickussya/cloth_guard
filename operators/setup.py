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

"""Setup / remove setup, and body mask create / delete operators."""

from __future__ import annotations

from bpy.types import Operator

from ..constants import (
    CG_MOD_BODY_MASK,
    CG_SHAPEKEY_LIVE,
    CG_VG_BODY_MASK,
    CG_VG_CLIPPING,
)
from ..core.mesh_utils import clear_vertex_group, ensure_vertex_group, is_mesh_object
from ..core.modifiers import ensure_body_mask_modifier
from ..core.shape_keys import ensure_live_correction_shapekey
from ._common import _settings, _temporary_mode_object, _validate_assigned_meshes, _warn_if_unusual_scale


class CG_OT_setup(Operator):
    bl_idname = "cloth_guard.setup"
    bl_label = "Setup Cloth Guard"
    bl_description = "Validate objects and prepare Cloth Guard data structures (vertex groups/body mask/live correction)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "cg_settings", None)
        if settings is None:
            return False
        return settings.body_object is not None and (
            len(getattr(settings, "garments", [])) > 0 or settings.garment_object is not None or settings.garment_collection is not None
        )

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated
        _warn_if_unusual_scale(self, settings, body_obj, context.evaluated_depsgraph_get())

        with _temporary_mode_object(context):
            ensure_vertex_group(body_obj, CG_VG_BODY_MASK)
            for garment_obj in garments:
                ensure_vertex_group(garment_obj, CG_VG_CLIPPING)

                # Optional control groups (only create if missing; safe defaults).
                for name in (
                    "CG_RiskArea",
                    "CG_Pinned",
                    "CG_Preserve_Collar",
                    "CG_Preserve_Hem",
                    "CG_Preserve_Seams",
                ):
                    if garment_obj.vertex_groups.get(name) is None:
                        garment_obj.vertex_groups.new(name=name)

            ensure_body_mask_modifier(body_obj)
            for garment_obj in garments:
                ensure_live_correction_shapekey(garment_obj)
                if garment_obj.data.shape_keys is not None:
                    kb = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_LIVE)
                    if kb is not None:
                        kb.value = 1.0 if settings.enable_live_anti_clip else 0.0

        self.report({"INFO"}, f"Cloth Guard setup complete ({len(garments)} garment(s))")
        return {"FINISHED"}


class CG_OT_remove_setup(Operator):
    bl_idname = "cloth_guard.remove_setup"
    bl_label = "Remove Cloth Guard Setup"
    bl_description = "Remove Cloth Guard modifiers/groups created by the add-on (keeps optional control groups)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        with _temporary_mode_object(context):
            mod = body_obj.modifiers.get(CG_MOD_BODY_MASK)
            if mod is not None:
                body_obj.modifiers.remove(mod)
            vg = body_obj.vertex_groups.get(CG_VG_BODY_MASK)
            if vg is not None:
                body_obj.vertex_groups.remove(vg)

            for garment_obj in garments:
                vg = garment_obj.vertex_groups.get(CG_VG_CLIPPING)
                if vg is not None:
                    garment_obj.vertex_groups.remove(vg)

                if garment_obj.data.shape_keys is not None:
                    kb = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_LIVE)
                    if kb is not None:
                        garment_obj.shape_key_remove(kb)

        self.report({"INFO"}, "Cloth Guard setup removed")
        return {"FINISHED"}


class CG_OT_create_body_mask(Operator):
    bl_idname = "cloth_guard.create_body_mask"
    bl_label = "Create Body Mask"
    bl_description = (
        "Current frame only: hides body vertices within Mask Distance of the garments on this frame. "
        "For an animation use Create Coverage Mask (Animation), which keeps skin that shows on any frame visible"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        validated = _validate_assigned_meshes(settings)
        if validated is None:
            self.report({"ERROR"}, "Assign a valid Body mesh and add garment mesh objects to the Garments list first")
            return {"CANCELLED"}
        body_obj, garments = validated

        # Vertex group writes require object mode.
        with _temporary_mode_object(context):
            return self._execute_object_mode(context, settings, body_obj, garments)

    def _execute_object_mode(self, context, settings, body_obj, garments):
        depsgraph = context.evaluated_depsgraph_get()
        body_eval = body_obj.evaluated_get(depsgraph)

        mask_dist = max(0.0, float(settings.mask_distance + settings.mask_expand))
        if mask_dist <= 0.0:
            self.report({"ERROR"}, "Mask distance must be > 0")
            return {"CANCELLED"}

        # Build a combined garment BVH in BODY LOCAL SPACE for stable queries.
        from mathutils.bvhtree import BVHTree

        body_mesh = body_eval.to_mesh()
        try:
            body_mesh.calc_loop_triangles()
            body_mw = body_eval.matrix_world
            body_mw_inv = body_mw.inverted_safe()

            combined_verts = []
            combined_tris = []
            vert_offset = 0

            for garment_obj in garments:
                garment_eval = garment_obj.evaluated_get(depsgraph)
                g_mesh = garment_eval.to_mesh()
                try:
                    g_mesh.calc_loop_triangles()
                    garment_to_body = body_mw_inv @ garment_eval.matrix_world

                    g_verts = [garment_to_body @ v.co for v in g_mesh.vertices]
                    g_tris = [tuple(vert_offset + vi for vi in lt.vertices) for lt in g_mesh.loop_triangles]

                    combined_verts.extend(g_verts)
                    combined_tris.extend(g_tris)
                    vert_offset += len(g_verts)
                finally:
                    garment_eval.to_mesh_clear()

            if not combined_verts or not combined_tris:
                self.report({"WARNING"}, "No garment triangles found in the selected collection")
                return {"CANCELLED"}

            bvh_garments = BVHTree.FromPolygons(combined_verts, combined_tris, all_triangles=True, epsilon=0.0)

            vg = ensure_vertex_group(body_obj, CG_VG_BODY_MASK)
            clear_vertex_group(body_obj, vg)

            b_mw = body_eval.matrix_world
            affected = 0
            for i, v in enumerate(body_mesh.vertices):
                world_co = b_mw @ v.co
                # Convert body vertex to body local for the combined BVH (it is already in body local, but keep explicit).
                body_local = body_mw_inv @ world_co
                nearest = bvh_garments.find_nearest(body_local)
                if nearest is None:
                    continue
                _, _, _, dist = nearest
                if dist is None:
                    continue
                if dist <= mask_dist:
                    vg.add([i], 1.0, "REPLACE")
                    affected += 1
        finally:
            body_eval.to_mesh_clear()

        ensure_body_mask_modifier(body_obj)
        self.report({"INFO"}, f"Body mask updated ({affected} vertices)")
        return {"FINISHED"}


class CG_OT_delete_body_mask(Operator):
    bl_idname = "cloth_guard.delete_body_mask"
    bl_label = "Delete Body Mask"
    bl_description = "Removes the Cloth Guard body mask and restores hidden body areas"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        body_obj = settings.body_object
        if body_obj is None or not is_mesh_object(body_obj):
            self.report({"ERROR"}, "Assign a valid Body mesh first")
            return {"CANCELLED"}

        with _temporary_mode_object(context):
            removed_mod = False
            removed_vg = False

            mod = body_obj.modifiers.get(CG_MOD_BODY_MASK)
            if mod is not None and getattr(mod, "type", None) == "MASK":
                body_obj.modifiers.remove(mod)
                removed_mod = True

            vg = body_obj.vertex_groups.get(CG_VG_BODY_MASK)
            if vg is not None:
                body_obj.vertex_groups.remove(vg)
                removed_vg = True

        if not removed_mod and not removed_vg:
            self.report({"INFO"}, "No Cloth Guard body mask found to delete")
            return {"CANCELLED"}

        parts = []
        if removed_vg:
            parts.append("vertex group")
        if removed_mod:
            parts.append("Mask modifier")
        self.report({"INFO"}, f"Deleted body mask ({' + '.join(parts)})")
        return {"FINISHED"}
