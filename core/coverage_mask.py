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

"""Animation-aware coverage mask: find body vertices that a garment covers on the sampled frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import bpy
from mathutils.bvhtree import BVHTree

from ..constants import CG_MOD_BODY_MASK, COVERAGE_RAY_START_OFFSET
from .body import _body_normal_sign

COVERAGE_MODES = ("ALL_FRAMES", "ANY_FRAME")


@dataclass(frozen=True)
class CoverageResult:
    masked: list[bool]  # per base body vertex: True = hide it
    masked_count: int
    protected_count: int  # vertices that would be masked but are in the protect group
    frames: list[int]


def coverage_frames(start: int, end: int, step: int) -> list[int]:
    """Sampled frames from start to end; the last frame is always included (missing a frame hides more)."""
    if end < start:
        start, end = end, start
    frames = list(range(start, end + 1, max(1, int(step))))
    if frames[-1] != end:
        frames.append(end)
    return frames


def _garments_bvh_world(garments: list[bpy.types.Object], depsgraph) -> BVHTree | None:
    """One BVH of all evaluated garments in WORLD space (None when there are no faces)."""
    verts = []
    tris = []
    for garment_obj in garments:
        g_eval = garment_obj.evaluated_get(depsgraph)
        mesh = g_eval.to_mesh()
        try:
            mesh.calc_loop_triangles()
            offset = len(verts)
            mw = g_eval.matrix_world
            verts.extend(mw @ v.co for v in mesh.vertices)
            tris.extend(tuple(offset + i for i in lt.vertices) for lt in mesh.loop_triangles)
        finally:
            g_eval.to_mesh_clear()
    if not tris:
        return None
    return BVHTree.FromPolygons(verts, tris, all_triangles=True, epsilon=0.0)


def compute_coverage_mask(
    *,
    scene: bpy.types.Scene,
    depsgraph,
    body_obj: bpy.types.Object,
    garments: list[bpy.types.Object],
    frames: list[int],
    outward_distance: float,
    inward_distance: float,
    mode: str = "ALL_FRAMES",
    protect_group: str = "",
    progress: Callable[[int], None] | None = None,
) -> CoverageResult:
    """
    Decide which body vertices to hide under the garments.

    On each sampled frame, a body vertex is "covered" when a ray from the skin along its normal
    hits a garment within outward_distance (the garment lies over the skin), or a ray into the body
    hits a garment within inward_distance (the skin pokes through the garment there).

    mode "ALL_FRAMES": hide a vertex only if it is covered on EVERY sampled frame (safe: skin that is
                       visible even once, like a hand that touches the coat once, stays visible).
    mode "ANY_FRAME":  hide a vertex covered on at least one frame (can hide visible skin).

    The existing CG_BodyMask modifier is switched off while measuring, so evaluated vertex indices
    match the base mesh. The original frame and the modifier's visibility are always restored.
    progress(i) is called before sampling the i-th frame.
    """
    if mode not in COVERAGE_MODES:
        raise ValueError(f"Unknown coverage mode {mode!r}")
    if not frames:
        # With no frames, ALL_FRAMES would hide every vertex (nothing ever proves it visible).
        raise ValueError("No frames to sample")
    vert_count = len(body_obj.data.vertices)

    protected: set[int] = set()
    if protect_group:
        vg = body_obj.vertex_groups.get(protect_group)
        if vg is None:
            raise RuntimeError(f"Protect group '{protect_group}' does not exist on the body '{body_obj.name}'")
        protected = {v.index for v in body_obj.data.vertices if any(g.group == vg.index and g.weight > 0.0 for g in v.groups)}

    # Body orientation: flip the normals if the body is inside-out (same rule as the anti-clip code).
    body_obj.data.calc_loop_triangles()
    normal_sign = _body_normal_sign(
        [v.co for v in body_obj.data.vertices], [tuple(lt.vertices) for lt in body_obj.data.loop_triangles]
    )

    hide_all = mode == "ALL_FRAMES"
    masked = [hide_all] * vert_count  # ALL: start True, any uncovered frame clears it; ANY: the opposite
    eps = COVERAGE_RAY_START_OFFSET

    mask_mod = body_obj.modifiers.get(CG_MOD_BODY_MASK)
    mask_mod_visible = mask_mod.show_viewport if mask_mod is not None else None
    prev_frame = scene.frame_current
    try:
        if mask_mod is not None:
            mask_mod.show_viewport = False
        for frame_index, frame in enumerate(frames):
            if progress is not None:
                progress(frame_index)
            scene.frame_set(frame)
            garment_bvh = _garments_bvh_world(garments, depsgraph)

            body_eval = body_obj.evaluated_get(depsgraph)
            mesh = body_eval.to_mesh()
            try:
                if len(mesh.vertices) != vert_count:
                    raise RuntimeError(
                        f"The body '{body_obj.name}' has a modifier that changes its vertex count "
                        f"({vert_count} -> {len(mesh.vertices)}), e.g. Subdivision. Disable it while creating the mask."
                    )
                # Blender 4.1 removed Mesh.calc_normals(); since then normals are always up to date.
                if bpy.app.version < (4, 1, 0):
                    mesh.calc_normals()
                mw = body_eval.matrix_world
                normal_matrix = mw.to_3x3().inverted_safe().transposed()
                for v in mesh.vertices:
                    i = v.index
                    if masked[i] != hide_all:
                        continue  # already decided: uncovered once (ALL) or covered once (ANY)
                    covered = False
                    if garment_bvh is not None:
                        p = mw @ v.co
                        n = (normal_matrix @ v.normal).normalized() * normal_sign
                        covered = (
                            garment_bvh.ray_cast(p + n * eps, n, outward_distance)[0] is not None
                            or garment_bvh.ray_cast(p - n * eps, -n, inward_distance)[0] is not None
                        )
                    if covered != hide_all:
                        masked[i] = covered
            finally:
                body_eval.to_mesh_clear()
    finally:
        if mask_mod is not None:
            mask_mod.show_viewport = mask_mod_visible
        scene.frame_set(prev_frame)

    protected_count = 0
    for i in protected:
        if masked[i]:
            masked[i] = False
            protected_count += 1
    return CoverageResult(
        masked=masked,
        masked_count=sum(1 for m in masked if m),
        protected_count=protected_count,
        frames=list(frames),
    )
