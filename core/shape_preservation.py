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

"""Rest shape storage, shape drift analysis and shape preservation (CG_LivePreserve)."""

from __future__ import annotations

from dataclasses import dataclass

import bpy
from mathutils import Vector

from ..constants import (
    CG_SHAPEKEY_REST,
    CG_VG_PINNED,
    CG_VG_PRESERVE_COLLAR,
    CG_VG_PRESERVE_HEM,
    CG_VG_PRESERVE_SEAMS,
    MASK_PRESERVE_GROUP_SCALE,
    PRESERVE_BOUNDARY_SCALE,
    PRESERVE_GROUP_SCALE,
    PRESERVE_MAX_DRIFT_REMOVAL,
)
from .mesh_utils import (
    _boundary_vertex_mask,
    _build_vertex_adjacency,
    _smooth_deltas_structural,
    _vector_safe_avg,
    _vertex_group_index,
    _vertex_weight,
)
from .shape_keys import (
    ensure_live_preserve_shapekey,
    ensure_rest_shape_shapekey,
    rest_space_deformation,
    shape_key_offsets,
    to_rest_space,
)


@dataclass(frozen=True)
class ShapeDriftStats:
    checked_verts: int
    flagged_verts: int
    max_drift_distance: float
    avg_flagged_drift: float | None


def store_rest_shape(
    *,
    garment_obj: bpy.types.Object,
    depsgraph,
    cage_eval_obj: bpy.types.Object | None = None,
) -> int:
    """
    Store the current evaluated (typically clean) garment shape into CG_RestShape (non-destructive).
    Returns number of vertices written.
    """
    if garment_obj.type != "MESH":
        raise RuntimeError("Garment is not a mesh")

    obj_eval = cage_eval_obj if cage_eval_obj is not None else garment_obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()
    try:
        base_mesh = garment_obj.data
        if len(mesh_eval.vertices) != len(base_mesh.vertices):
            raise RuntimeError("Rest shape storage requires a topology-stable cage evaluation")

        kb = ensure_rest_shape_shapekey(garment_obj)
        for i in range(len(base_mesh.vertices)):
            kb.data[i].co = mesh_eval.vertices[i].co
        kb.value = 0.0
        kb.mute = True
        return len(base_mesh.vertices)
    finally:
        obj_eval.to_mesh_clear()


def analyze_shape_drift(
    *,
    garment_obj: bpy.types.Object,
    depsgraph,
    drift_threshold: float,
    cage_eval_obj: bpy.types.Object | None = None,
    protect_borders: bool = True,
    protect_scale: float = 0.5,
) -> tuple[ShapeDriftStats, list[float]]:
    """
    Compare current evaluated cage shape to CG_RestShape and produce per-vertex drift weights.
    Returns (stats, weights) suitable for writing into CG_ShapeDrift.
    """
    if garment_obj.data.shape_keys is None or garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_REST) is None:
        raise RuntimeError("Missing rest shape. Click Store Rest Shape first.")

    rest = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_REST)
    if rest is None:
        raise RuntimeError("Missing rest shape. Click Store Rest Shape first.")

    obj_eval = cage_eval_obj if cage_eval_obj is not None else garment_obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()
    try:
        base_mesh = garment_obj.data
        if len(mesh_eval.vertices) != len(base_mesh.vertices) or len(rest.data) != len(base_mesh.vertices):
            raise RuntimeError("Shape drift analysis requires a topology-stable cage evaluation")

        drift_threshold = max(1e-8, float(drift_threshold))
        boundary_mask = _boundary_vertex_mask(base_mesh) if protect_borders else [False] * len(base_mesh.vertices)

        weights = [0.0] * len(base_mesh.vertices)
        flagged = 0
        max_drift = 0.0
        flagged_drifts: list[float] = []

        for i in range(len(base_mesh.vertices)):
            d = mesh_eval.vertices[i].co - rest.data[i].co
            dist = float(d.length)
            max_drift = max(max_drift, dist)
            if dist <= drift_threshold:
                continue
            w = max(0.0, min(1.0, (dist - drift_threshold) / max(drift_threshold, 1e-8)))
            if boundary_mask[i]:
                w *= float(protect_scale)
            if w > 0.0:
                flagged += 1
                flagged_drifts.append(dist)
                weights[i] = w

        stats = ShapeDriftStats(
            checked_verts=len(base_mesh.vertices),
            flagged_verts=flagged,
            max_drift_distance=max_drift,
            avg_flagged_drift=_vector_safe_avg(flagged_drifts),
        )
        return stats, weights
    finally:
        obj_eval.to_mesh_clear()


def generate_shape_preservation(
    *,
    garment_obj: bpy.types.Object,
    depsgraph,
    strength: float,
    smoothing_iterations: int,
    smoothing_strength: float,
    volume_preservation: float,
    silhouette_preservation: float,
    drift_threshold: float,
    protect_borders: bool,
    protect_groups: bool,
    cage_eval_obj: bpy.types.Object | None = None,
) -> tuple[int, float, int]:
    """
    Generate/update CG_LivePreserve to reduce high-frequency drift compared to CG_RestShape.
    Returns (changed_verts, max_delta_world_m, unconvertible_verts).
    """
    if garment_obj.type != "MESH":
        raise RuntimeError("Garment is not a mesh")
    if garment_obj.data.shape_keys is None or garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_REST) is None:
        raise RuntimeError("Missing rest shape. Click Store Rest Shape first.")

    strength = max(0.0, min(1.0, float(strength)))
    smoothing_iterations = max(0, int(smoothing_iterations))
    smoothing_strength = max(0.0, min(1.0, float(smoothing_strength)))
    volume_preservation = max(0.0, min(1.0, float(volume_preservation)))
    silhouette_preservation = max(0.0, min(1.0, float(silhouette_preservation)))
    drift_threshold = max(1e-8, float(drift_threshold))

    rest = garment_obj.data.shape_keys.key_blocks.get(CG_SHAPEKEY_REST)
    if rest is None:
        raise RuntimeError("Missing rest shape. Click Store Rest Shape first.")

    obj_eval = cage_eval_obj if cage_eval_obj is not None else garment_obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()
    try:
        # Blender 4.1 removed Mesh.calc_normals(); since then normals are always up to date.
        if bpy.app.version < (4, 1, 0):
            mesh_eval.calc_normals()
        base_mesh = garment_obj.data
        if len(mesh_eval.vertices) != len(base_mesh.vertices) or len(rest.data) != len(base_mesh.vertices):
            raise RuntimeError("Shape preservation requires a topology-stable cage evaluation")

        drift: list[Vector] = [Vector((0.0, 0.0, 0.0)) for _ in range(len(base_mesh.vertices))]
        for i in range(len(base_mesh.vertices)):
            drift[i] = mesh_eval.vertices[i].co - rest.data[i].co

        adjacency = _build_vertex_adjacency(base_mesh)
        rest_normals = [v.normal.copy() for v in base_mesh.vertices]
        smoothed = _smooth_deltas_structural(
            deltas=drift,
            adjacency=adjacency,
            rest_normals=rest_normals,
            drift_threshold=drift_threshold,
            iterations=int(smoothing_iterations),
            strength=float(smoothing_strength),
        )

        boundary_mask = _boundary_vertex_mask(base_mesh) if protect_borders else [False] * len(base_mesh.vertices)
        boundary_scale = PRESERVE_BOUNDARY_SCALE

        pinned_idx = _vertex_group_index(garment_obj, CG_VG_PINNED) if protect_groups else None
        collar_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_COLLAR) if protect_groups else None
        hem_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_HEM) if protect_groups else None
        seams_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_SEAMS) if protect_groups else None
        preserve_scale = PRESERVE_GROUP_SCALE

        changed = 0
        max_delta_world = 0.0
        mw3 = garment_obj.matrix_world.to_3x3()
        corrections: list[Vector] = []

        for i in range(len(base_mesh.vertices)):
            corr = (smoothed[i] - drift[i]) * strength

            # Prevent inward collapse: reduce inward normal component relative to the CURRENT pose normal.
            cn = mesh_eval.vertices[i].normal
            if cn.length > 1e-12:
                cn = cn.normalized()
                dotn = float(corr.dot(cn))
                cn_comp = cn * dotn
                ct_comp = corr - cn_comp
                if dotn < 0.0:
                    cn_comp *= float(1.0 - volume_preservation)
                corr = ct_comp + cn_comp

            # Silhouette preservation: additionally reduce normal component in rest space (helps keep thickness/opening).
            rn = base_mesh.vertices[i].normal
            if rn.length > 1e-12:
                rn = rn.normalized()
                dotr = float(corr.dot(rn))
                rn_comp = rn * dotr
                rt_comp = corr - rn_comp
                corr = rt_comp + rn_comp * float(1.0 - silhouette_preservation)

            scale = 1.0
            if pinned_idx is not None:
                pw = _vertex_weight(base_mesh.vertices[i], pinned_idx)
                if pw > 0.0:
                    scale *= max(0.0, 1.0 - pw)

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

            corr *= scale

            # Clamp: never remove more than a fraction of the local drift magnitude (avoids global pull/size shrink).
            drift_len = float(drift[i].length)
            corr_len = float(corr.length)
            if drift_len > 1e-8 and corr_len > drift_len * PRESERVE_MAX_DRIFT_REMOVAL:
                corr *= (drift_len * PRESERVE_MAX_DRIFT_REMOVAL) / max(corr_len, 1e-12)

            corrections.append(corr)

            ln = float(corr.length)
            if ln > 1e-12:
                changed += 1
                max_delta_world = max(max_delta_world, float((mw3 @ corr).length))
    finally:
        # Free the evaluated mesh before probing below (probing re-evaluates the scene).
        obj_eval.to_mesh_clear()

    # Measured on the posed garment, but the shape key is applied before the Armature: convert back.
    corrections, unconvertible = to_rest_space(corrections, rest_space_deformation(garment_obj, depsgraph))

    live = ensure_live_preserve_shapekey(garment_obj)
    basis = garment_obj.data.shape_keys.key_blocks[0]
    # The measured drift already includes the live key at its current value; add on top of it.
    already_applied = shape_key_offsets(live, basis)
    for i, corr in enumerate(corrections):
        live.data[i].co = basis.data[i].co + already_applied[i] + corr
    live.value = 1.0
    return changed, max_delta_world, unconvertible


def compute_shape_preserve_mask_weights(
    garment_obj: bpy.types.Object,
    *,
    protect_groups: bool,
) -> list[float]:
    """
    Build a per-vertex mask for shape preservation modifiers.

    This is designed to work even when the garment has topology-changing modifiers
    (weights will be propagated by modifiers like Subdivision).
    """
    base_mesh = garment_obj.data
    weights = [1.0] * len(base_mesh.vertices)

    if not protect_groups:
        return weights

    pinned_idx = _vertex_group_index(garment_obj, CG_VG_PINNED)
    collar_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_COLLAR)
    hem_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_HEM)
    seams_idx = _vertex_group_index(garment_obj, CG_VG_PRESERVE_SEAMS)

    for i, v in enumerate(base_mesh.vertices):
        scale = 1.0
        if pinned_idx is not None:
            pw = _vertex_weight(v, pinned_idx)
            if pw > 0.0:
                scale *= max(0.0, 1.0 - pw)

        preserve_w = 0.0
        if collar_idx is not None:
            preserve_w = max(preserve_w, _vertex_weight(v, collar_idx))
        if hem_idx is not None:
            preserve_w = max(preserve_w, _vertex_weight(v, hem_idx))
        if seams_idx is not None:
            preserve_w = max(preserve_w, _vertex_weight(v, seams_idx))
        if preserve_w > 0.0:
            # Strongly protect these regions.
            scale *= max(0.0, 1.0 - preserve_w * MASK_PRESERVE_GROUP_SCALE)

        weights[i] *= scale

    return weights
