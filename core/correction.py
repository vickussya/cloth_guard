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

"""Anti-clip correction of the current pose, written into CG_LiveCorrection."""

from __future__ import annotations

from dataclasses import dataclass

import bpy
from mathutils import Vector

from ..constants import (
    CG_VG_PINNED,
    CG_VG_PRESERVE_COLLAR,
    CG_VG_PRESERVE_HEM,
    CG_VG_PRESERVE_SEAMS,
    CG_VG_RISK,
    CORRECTION_BOUNDARY_SCALE,
    CORRECTION_PRESERVE_SCALE,
    PENETRATION_EPS_FRACTION,
)
from .body import BodySurface, build_body_surface
from .mesh_utils import (
    _boundary_vertex_mask,
    _build_vertex_adjacency,
    _safe_min,
    _smooth_deltas,
    _vertex_group_index,
    _vertex_weight,
)
from .shape_keys import (
    ensure_live_correction_shapekey,
    rest_space_deformation,
    shape_key_offsets,
    to_rest_space,
)


@dataclass(frozen=True)
class PoseCorrectionStats:
    checked_verts: int
    corrected_verts: int
    min_nearest_distance: float | None
    changed_verts: int
    max_delta_distance: float
    # Pushed vertices whose deformation could not be undone (see to_rest_space); they keep the posed push.
    unconvertible_verts: int = 0


def correct_current_pose(
    *,
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    depsgraph,
    offset_distance: float,
    detection_radius: float,
    correction_strength: float,
    max_push_distance: float,
    smooth_iterations: int,
    smooth_strength: float,
    use_risk_area: bool,
    preserve_pinned_areas: bool,
    body_surface: BodySurface | None = None,
) -> PoseCorrectionStats:
    offset_distance = max(0.0, float(offset_distance))
    detection_radius = max(offset_distance, float(detection_radius))
    correction_strength = max(0.0, min(1.0, float(correction_strength)))
    max_push_distance = max(0.0, float(max_push_distance))
    if body_surface is None:
        body_surface = build_body_surface(body_obj, depsgraph)

    garment_eval = garment_obj.evaluated_get(depsgraph)
    garment_mesh = garment_eval.to_mesh()
    try:
        base_mesh = garment_obj.data
        if len(garment_mesh.vertices) != len(base_mesh.vertices):
            raise RuntimeError(
                "Garment evaluated mesh vertex count differs from base mesh. "
                "For MVP correction, keep the garment modifier stack topology-stable (Armature/shape keys are OK; Subdivision/Remesh are not)."
            )

        body_mw = body_surface.matrix_world
        garment_mw = garment_eval.matrix_world
        garment_to_body = body_mw.inverted_safe() @ garment_mw

        # BVH is in evaluated BODY LOCAL SPACE.
        bvh_body = body_surface.bvh

        inv_garment_world = garment_obj.matrix_world.inverted_safe()

        risk_idx = _vertex_group_index(garment_obj, CG_VG_RISK) if use_risk_area else None
        pinned_idx = _vertex_group_index(garment_obj, CG_VG_PINNED) if preserve_pinned_areas else None
        collar_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_COLLAR)
        hem_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_HEM)
        seams_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_SEAMS)

        boundary_mask = _boundary_vertex_mask(base_mesh)
        boundary_scale = CORRECTION_BOUNDARY_SCALE

        preserve_scale = CORRECTION_PRESERVE_SCALE
        penetration_eps = max(1e-6, offset_distance * PENETRATION_EPS_FRACTION)

        deltas: list[Vector] = [Vector((0.0, 0.0, 0.0)) for _ in range(len(garment_mesh.vertices))]
        checked = 0
        corrected = 0
        nearest_distances: list[float | None] = [None] * len(garment_mesh.vertices)

        body_normal_sign = body_surface.normal_sign

        def _choose_outward_normal(normal_body: Vector) -> Vector:
            return normal_body.normalized() * body_normal_sign

        def _signed_distance(point_body: Vector, nearest_co: Vector, nearest_no: Vector | None) -> float:
            # Positive = outside the body, negative = inside.
            # Without a surface normal the side is unknown, so we assume "outside" and return the
            # plain distance: such a vertex only gets the gentle near-contact push, never a deep push.
            vec = point_body - nearest_co
            if nearest_no is None:
                return float(vec.length)
            return float(vec.dot(_choose_outward_normal(nearest_no)))

        for i, v in enumerate(garment_mesh.vertices):
            if risk_idx is not None and _vertex_weight(base_mesh.vertices[i], risk_idx) <= 0.0:
                continue

            checked += 1
            garment_in_body = garment_to_body @ v.co
            nearest = bvh_body.find_nearest(garment_in_body)
            if nearest is None:
                continue
            nearest_co, nearest_no, _, dist = nearest
            if dist is None:
                dist = (garment_in_body - nearest_co).length
            dist = float(dist)
            nearest_distances[i] = dist

            signed = _signed_distance(garment_in_body, nearest_co, nearest_no)
            inside = signed < -penetration_eps

            # Skip vertices that are far away AND outside. A vertex deep inside the body is also
            # "far" from the surface, so the unsigned distance alone must not skip it.
            if dist > detection_radius and not inside:
                continue

            if nearest_no is not None:
                push_dir_body = _choose_outward_normal(nearest_no)
            else:
                vec = garment_in_body - nearest_co
                push_dir_body = vec.normalized() if vec.length > 1e-12 else Vector((0.0, 0.0, 1.0))

            if inside:
                push_amount = offset_distance + (-signed)
            else:
                if dist >= offset_distance:
                    continue
                push_amount = offset_distance - dist

            if max_push_distance > 0.0:
                push_amount = min(push_amount, max_push_distance)

            push_amount *= correction_strength
            if not inside and offset_distance > 1e-8:
                # Softer influence for "near contact" (outside) compared to penetration.
                tight = max(0.0, min(1.0, (offset_distance - dist) / offset_distance))
                push_amount *= tight
            if push_amount <= 0.0:
                continue

            # Group-based attenuation.
            scale = 1.0
            if pinned_idx is not None:
                pinned_w = _vertex_weight(base_mesh.vertices[i], pinned_idx)
                if pinned_w > 0.0:
                    scale *= max(0.0, 1.0 - pinned_w)

            preserve_w = 0.0
            if collar_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], collar_idx))
            if hem_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], hem_idx))
            if seams_idx is not None:
                preserve_w = max(preserve_w, _vertex_weight(base_mesh.vertices[i], seams_idx))
            if preserve_w > 0.0:
                scale *= max(0.0, 1.0 - preserve_w * preserve_scale)

            if boundary_mask[i]:
                scale *= boundary_scale

            if scale <= 0.0:
                continue

            # Per-vertex safety: only keep the move if the vertex ends up further OUTSIDE the body.
            push_dir_body_n = push_dir_body.normalized()
            delta_body = push_dir_body_n * (push_amount * scale)
            new_pos = garment_in_body + delta_body
            n2 = bvh_body.find_nearest(new_pos)
            if n2 is not None:
                signed_after = _signed_distance(new_pos, n2[0], n2[1])
                if signed_after <= signed + 1e-8:
                    continue

            push_dir_world = (body_mw.to_3x3() @ push_dir_body_n).normalized()
            world_delta = push_dir_world * (push_amount * scale)
            local_delta = inv_garment_world.to_3x3() @ world_delta
            deltas[i] = local_delta
            corrected += 1

        # Smooth deltas over the garment topology.
        deltas = _smooth_deltas(
            deltas=deltas,
            adjacency=_build_vertex_adjacency(base_mesh),
            iterations=int(smooth_iterations),
            strength=float(smooth_strength),
        )

        # Compute delta stats after smoothing (what will be written to the shape key).
        changed = 0
        max_delta = 0.0
        mw3 = garment_obj.matrix_world.to_3x3()
        for d in deltas:
            ln = float(d.length)
            if ln > 1e-12:
                changed += 1
                wd = mw3 @ d
                max_delta = max(max_delta, float(wd.length))
    finally:
        # Free the evaluated mesh before probing below (probing re-evaluates the scene).
        garment_eval.to_mesh_clear()

    # The pushes were measured on the posed garment, but a shape key is applied BEFORE the
    # Armature (and other deforming modifiers). Convert them back so they still point outward
    # after the rig re-poses them.
    deltas, unconvertible = to_rest_space(deltas, rest_space_deformation(garment_obj, depsgraph))

    live = ensure_live_correction_shapekey(garment_obj)
    basis = garment_obj.data.shape_keys.key_blocks[0]
    # The pose we measured already contains the live key at its current value,
    # so the new push goes on top of that (not on top of Basis, which would drop it).
    already_applied = shape_key_offsets(live, basis)
    for i in range(len(basis.data)):
        live.data[i].co = basis.data[i].co + already_applied[i] + deltas[i]
    live.value = 1.0

    return PoseCorrectionStats(
        checked_verts=checked,
        corrected_verts=corrected,
        min_nearest_distance=_safe_min(nearest_distances),
        changed_verts=changed,
        max_delta_distance=max_delta,
        unconvertible_verts=unconvertible,
    )
