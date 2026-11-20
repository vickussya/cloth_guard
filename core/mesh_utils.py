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

"""Mesh helpers: vertex groups and weights, adjacency, boundary mask, delta smoothing."""

from __future__ import annotations

from typing import Iterable, Optional

import bpy
from mathutils import Vector

from ..constants import DRIFT_SIMILARITY_RANGE


def is_mesh_object(obj) -> bool:
    return obj is not None and getattr(obj, "type", None) == "MESH"


def ensure_vertex_group(obj: bpy.types.Object, name: str) -> bpy.types.VertexGroup:
    vg = obj.vertex_groups.get(name)
    if vg is None:
        vg = obj.vertex_groups.new(name=name)
    return vg


def clear_vertex_group(obj: bpy.types.Object, vg: bpy.types.VertexGroup) -> None:
    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "vertices"):
        return
    count = len(mesh.vertices)
    if count == 0:
        return
    indices = list(range(count))
    try:
        vg.remove(indices)
    except RuntimeError:
        for i in indices:
            try:
                vg.remove([i])
            except RuntimeError:
                # This vertex is not in the group: nothing to remove (expected, not an error).
                continue


def _vertex_group_index(obj: bpy.types.Object, group_name: str) -> Optional[int]:
    vg = obj.vertex_groups.get(group_name)
    return vg.index if vg is not None else None


def _vertex_weight(v: bpy.types.MeshVertex, group_index: int) -> float:
    for g in v.groups:
        if g.group == group_index:
            return float(g.weight)
    return 0.0


def _boundary_vertex_mask(mesh: bpy.types.Mesh) -> list[bool]:
    """
    Return a boolean mask of boundary vertices for the given base mesh.

    We intentionally avoid relying on MeshEdge.is_boundary (not present in all
    Blender versions) and compute boundary edges by counting polygon edge usage.
    """
    boundary = [False] * len(mesh.vertices)
    if len(mesh.vertices) == 0:
        return boundary

    edge_use: dict[tuple[int, int], int] = {}
    for poly in mesh.polygons:
        for a, b in poly.edge_keys:
            key = (int(a), int(b))
            edge_use[key] = edge_use.get(key, 0) + 1

    for (a, b), count in edge_use.items():
        if count == 1:
            boundary[a] = True
            boundary[b] = True
    return boundary


def _safe_min(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return min(vals) if vals else None


def _safe_avg(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _build_vertex_adjacency(mesh: bpy.types.Mesh) -> list[list[int]]:
    adj: list[list[int]] = [[] for _ in range(len(mesh.vertices))]
    for e in mesh.edges:
        a = int(e.vertices[0])
        b = int(e.vertices[1])
        adj[a].append(b)
        adj[b].append(a)
    return adj


def _smooth_deltas(
    *,
    deltas: list[Vector],
    adjacency: list[list[int]],
    iterations: int,
    strength: float,
) -> list[Vector]:
    if iterations <= 0 or strength <= 0.0:
        return deltas
    strength = max(0.0, min(1.0, float(strength)))

    cur = deltas
    for _ in range(int(iterations)):
        nxt = [v.copy() for v in cur]
        for i, nbrs in enumerate(adjacency):
            if not nbrs:
                continue
            avg = Vector((0.0, 0.0, 0.0))
            for j in nbrs:
                avg += cur[j]
            avg /= float(len(nbrs))
            nxt[i] = cur[i].lerp(avg, strength)
        cur = nxt
    return cur


def _smooth_deltas_structural(
    *,
    deltas: list[Vector],
    adjacency: list[list[int]],
    rest_normals: list[Vector],
    drift_threshold: float,
    iterations: int,
    strength: float,
    normal_cos_limit: float = 0.5,
) -> list[Vector]:
    """
    Structure-aware smoothing for drift fields.

    - Avoids smoothing across sharp normal changes (collars/edges) using a cosine limit.
    - Avoids removing large-scale deformation by down-weighting edges where drift differs a lot.
    """
    if iterations <= 0 or strength <= 0.0:
        return deltas
    strength = max(0.0, min(1.0, float(strength)))
    drift_threshold = max(1e-8, float(drift_threshold))

    # Pre-normalize normals.
    rn = []
    for n in rest_normals:
        rn.append(n.normalized() if n.length > 1e-12 else Vector((0.0, 0.0, 1.0)))

    cur = deltas
    for _ in range(int(iterations)):
        nxt = [v.copy() for v in cur]
        for i, nbrs in enumerate(adjacency):
            if not nbrs:
                continue
            acc = Vector((0.0, 0.0, 0.0))
            wsum = 0.0
            ni = rn[i]
            for j in nbrs:
                nj = rn[j]
                nd = float(ni.dot(nj))
                if nd < normal_cos_limit:
                    continue
                # Drift similarity: if drift varies a lot across an edge, treat it as large-scale deformation and smooth less.
                dd = float((cur[j] - cur[i]).length)
                sim = max(0.0, 1.0 - (dd / (drift_threshold * DRIFT_SIMILARITY_RANGE)))
                w = nd * sim
                if w <= 0.0:
                    continue
                acc += cur[j] * w
                wsum += w
            if wsum > 1e-12:
                avg = acc / wsum
                nxt[i] = cur[i].lerp(avg, strength)
        cur = nxt
    return cur


def write_weights_to_vertex_group(
    obj: bpy.types.Object, group_name: str, weights: Iterable[float]
) -> int:
    vg = ensure_vertex_group(obj, group_name)
    clear_vertex_group(obj, vg)

    affected = 0
    for i, w in enumerate(weights):
        if w > 0.0:
            vg.add([i], float(w), "REPLACE")
            affected += 1
    return affected


def _vector_safe_avg(vals: Iterable[float]) -> float | None:
    arr = [float(v) for v in vals]
    return (sum(arr) / len(arr)) if arr else None


def _neighbor_rings(adjacency: list[list[int]], start: int, rings: int) -> set[int]:
    if rings <= 0:
        return {start}
    visited = {start}
    frontier = {start}
    for _ in range(int(rings)):
        nxt = set()
        for v in frontier:
            for nb in adjacency[v]:
                if nb not in visited:
                    visited.add(nb)
                    nxt.add(nb)
        frontier = nxt
        if not frontier:
            break
    return visited


def evaluated_world_size(obj: bpy.types.Object, depsgraph) -> float:
    """
    Largest side (metres) of the evaluated object's world-space bounding box.

    Used as "how tall is the character": the largest side also works for a character that is
    lying down or in a wide T-pose. Includes object scale, parent scale and the rig.
    """
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()
    try:
        if len(mesh.vertices) == 0:
            return 0.0
        mw = obj_eval.matrix_world
        points = [mw @ v.co for v in mesh.vertices]
        return max(max(p[axis] for p in points) - min(p[axis] for p in points) for axis in range(3))
    finally:
        obj_eval.to_mesh_clear()
