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

"""Shape-key helpers (live keys, baking) and conversion of posed pushes back to rest space."""

from __future__ import annotations

import bpy
from mathutils import Matrix, Vector

from ..constants import (
    CG_SHAPEKEY_LIVE,
    CG_SHAPEKEY_LIVE_PRESERVE,
    CG_SHAPEKEY_PROBE,
    CG_SHAPEKEY_REST,
    DEFORM_MAX_AMPLIFICATION,
    DEFORM_MIN_DETERMINANT,
    DEFORM_PROBE_STEP_FRACTION,
)


def _ensure_shape_keys(obj: bpy.types.Object) -> bpy.types.Key:
    if obj.data.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)
    return obj.data.shape_keys


def ensure_live_correction_shapekey(garment_obj: bpy.types.Object) -> bpy.types.ShapeKey:
    keys = _ensure_shape_keys(garment_obj)
    kb = keys.key_blocks.get(CG_SHAPEKEY_LIVE)
    if kb is None:
        kb = garment_obj.shape_key_add(name=CG_SHAPEKEY_LIVE, from_mix=False)
    return kb


def ensure_rest_shape_shapekey(garment_obj: bpy.types.Object) -> bpy.types.ShapeKey:
    keys = _ensure_shape_keys(garment_obj)
    kb = keys.key_blocks.get(CG_SHAPEKEY_REST)
    if kb is None:
        kb = garment_obj.shape_key_add(name=CG_SHAPEKEY_REST, from_mix=False)
        kb.value = 0.0
        kb.mute = True
    return kb


def ensure_live_preserve_shapekey(garment_obj: bpy.types.Object) -> bpy.types.ShapeKey:
    keys = _ensure_shape_keys(garment_obj)
    kb = keys.key_blocks.get(CG_SHAPEKEY_LIVE_PRESERVE)
    if kb is None:
        kb = garment_obj.shape_key_add(name=CG_SHAPEKEY_LIVE_PRESERVE, from_mix=False)
    return kb


def shape_key_offsets(kb: bpy.types.ShapeKey, basis: bpy.types.ShapeKey) -> list[Vector]:
    """
    Per vertex: what this shape key currently adds on top of Basis (offset x value).
    A muted key adds nothing.
    """
    factor = 0.0 if kb.mute else float(kb.value)
    return [(kb.data[i].co - basis.data[i].co) * factor for i in range(len(basis.data))]


def reset_shape_key_to_basis(kb: bpy.types.ShapeKey, basis: bpy.types.ShapeKey) -> None:
    for i in range(len(basis.data)):
        kb.data[i].co = basis.data[i].co


def bake_live_key_into(garment_obj: bpy.types.Object, live_name: str, target_name: str) -> bpy.types.ShapeKey:
    """
    Move a live correction key (CG_LiveCorrection / CG_LivePreserve) into a per-frame key.

    New target = what the target key already adds + what the live key adds. The live key is then
    reset to Basis, so the correction is counted once (in the target key), not twice.
    Only the live key's own offset is copied: the user's other shape keys are not baked in.
    """
    keys = _ensure_shape_keys(garment_obj)
    basis = keys.key_blocks[0]
    live = keys.key_blocks.get(live_name)
    if live is None:
        raise RuntimeError(f"{live_name} shape key is missing")

    live_offsets = shape_key_offsets(live, basis)
    target = keys.key_blocks.get(target_name)
    if target is None:
        target = garment_obj.shape_key_add(name=target_name, from_mix=False)
        target_offsets = [Vector((0.0, 0.0, 0.0))] * len(basis.data)
    else:
        target_offsets = shape_key_offsets(target, basis)

    for i in range(len(basis.data)):
        target.data[i].co = basis.data[i].co + target_offsets[i] + live_offsets[i]
    reset_shape_key_to_basis(live, basis)
    return target


def _evaluated_local_coords(obj: bpy.types.Object, depsgraph) -> list[Vector]:
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()
    try:
        return [v.co.copy() for v in mesh.vertices]
    finally:
        obj_eval.to_mesh_clear()


def rest_space_deformation(garment_obj: bpy.types.Object, depsgraph) -> list[Matrix] | None:
    """
    Per vertex: a 3x3 matrix J with   posed offset = J @ shape-key offset.

    Shape keys are applied before the Armature (and other deforming modifiers), so J says what the
    rig does to a small move written into a shape key. It is measured, not computed from bones:
    a temporary probe key moves every vertex one small step along X, then Y, then Z, and we read
    where the evaluated vertex ends up. Works for any modifier stack that keeps the vertex count.

    Returns None when no modifier is enabled (then J is the identity: nothing to convert).
    """
    if not any(m.show_viewport for m in garment_obj.modifiers):
        return None

    keys = _ensure_shape_keys(garment_obj)
    basis = keys.key_blocks[0]
    lo = Vector((min(p.co.x for p in basis.data), min(p.co.y for p in basis.data), min(p.co.z for p in basis.data)))
    hi = Vector((max(p.co.x for p in basis.data), max(p.co.y for p in basis.data), max(p.co.z for p in basis.data)))
    step = max(1e-6, (hi - lo).length * DEFORM_PROBE_STEP_FRACTION)

    base = _evaluated_local_coords(garment_obj, depsgraph)
    probe = garment_obj.shape_key_add(name=CG_SHAPEKEY_PROBE, from_mix=False)
    probe.value = 1.0
    columns = []
    try:
        for axis in (Vector((step, 0.0, 0.0)), Vector((0.0, step, 0.0)), Vector((0.0, 0.0, step))):
            for i, point in enumerate(probe.data):
                point.co = basis.data[i].co + axis
            depsgraph.update()
            moved = _evaluated_local_coords(garment_obj, depsgraph)
            columns.append([(moved[i] - base[i]) / step for i in range(len(base))])
    finally:
        garment_obj.shape_key_remove(probe)
        depsgraph.update()

    return [Matrix((columns[0][i], columns[1][i], columns[2][i])).transposed() for i in range(len(base))]


def to_rest_space(deltas: list[Vector], deformation: list[Matrix] | None) -> tuple[list[Vector], int]:
    """
    Undo the deformation on posed-space pushes: rest push = J^-1 @ posed push.

    Returns (converted pushes, unconvertible count). A vertex whose J is flat or would blow the push
    up by more than DEFORM_MAX_AMPLIFICATION keeps its posed push (the old behaviour) and is counted,
    so the operator can warn the artist.
    """
    if deformation is None:
        return deltas, 0
    out: list[Vector] = []
    unconvertible = 0
    for d, j in zip(deltas, deformation):
        if d.length_squared == 0.0:
            out.append(d)
            continue
        rest = j.inverted_safe() @ d if abs(j.determinant()) >= DEFORM_MIN_DETERMINANT else None
        if rest is None or rest.length > d.length * DEFORM_MAX_AMPLIFICATION:
            out.append(d)
            unconvertible += 1
        else:
            out.append(rest)
    return out, unconvertible
