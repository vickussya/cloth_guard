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

"""The evaluated body as a searchable surface (BVH) and its normal orientation, shared per frame."""

from __future__ import annotations

from dataclasses import dataclass

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree


def build_bvh(obj_eval: bpy.types.Object, depsgraph) -> BVHTree:
    return BVHTree.FromObject(obj_eval, depsgraph, epsilon=0.0)


@dataclass(frozen=True)
class BodySurface:
    """
    The evaluated body on one frame, ready for nearest-point queries.

    Building the BVH is the expensive part, and the body does not change while garments are
    checked and corrected on the same frame, so operators build this once per frame and pass it in.
    """

    bvh: BVHTree  # in evaluated body LOCAL space
    matrix_world: Matrix
    normal_sign: float  # +1 normals point out of the body, -1 the body is inside-out (see _body_normal_sign)


def build_body_surface(body_obj: bpy.types.Object, depsgraph) -> BodySurface:
    body_eval = body_obj.evaluated_get(depsgraph)
    mesh = body_eval.to_mesh()
    try:
        mesh.calc_loop_triangles()
        verts = [v.co.copy() for v in mesh.vertices]
        tris = [tuple(lt.vertices) for lt in mesh.loop_triangles]
        return BodySurface(
            bvh=BVHTree.FromPolygons(verts, tris, all_triangles=True, epsilon=0.0),
            matrix_world=body_eval.matrix_world.copy(),
            normal_sign=_body_normal_sign(verts, tris),
        )
    finally:
        body_eval.to_mesh_clear()


def _signed_volume(verts: list[Vector], tris: list[tuple[int, int, int]]) -> float:
    """
    Signed volume of a triangle mesh (sum of the tetrahedra from the origin to every triangle).

    Positive when the face normals point outward, negative when the mesh is inside-out.
    Small holes (neck, wrists) barely change the result, so the sign stays reliable for bodies.
    """
    total = 0.0
    for a, b, c in tris:
        total += verts[a].dot(verts[b].cross(verts[c]))
    return total / 6.0  # each triple product is 6x the volume of its tetrahedron


def _body_normal_sign(verts: list[Vector], tris: list[tuple[int, int, int]]) -> float:
    """
    +1.0 if the body's face normals point outward, -1.0 if the body is inside-out (flipped normals).

    Multiply every BVH normal by this so that "positive = outside" holds for both cases.
    (Comparing distances a small step along +n / -n cannot do this: for a point INSIDE the
    body, stepping inward also increases the distance, so it would pick the inward side.)
    """
    return 1.0 if _signed_volume(verts, tris) >= 0.0 else -1.0
