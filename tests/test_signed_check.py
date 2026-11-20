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

"""
Background test for the signed inside/outside check in correct_current_pose.

Run from the repository root:
    blender -b --factory-startup --python-exit-code 1 --python tests/test_signed_check.py

A UV sphere "body" (radius 1) and a smaller UV sphere "garment" sit at the
origin, so every garment vertex is inside the body. After correct_current_pose
every garment vertex must be outside the body and no vertex may move inward.

Inside/outside is measured independently of the add-on: for a sphere centred
at the origin, the true outward direction of a body face is the one pointing
away from the origin, whatever way the face normal is stored.
"""

import importlib
import os
import sys

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(REPO_DIR))
cg_detection = importlib.import_module(os.path.basename(REPO_DIR) + ".core.detection")
cg_correction = importlib.import_module(os.path.basename(REPO_DIR) + ".core.correction")

OFFSET = 0.01  # 1 cm, as in the bug report
DETECTION_RADIUS = 0.02  # add-on default
MAX_PUSH = 0.05  # large enough that the cap never limits these cases
BODY_RADIUS = 1.0


def _make_sphere(name: str, radius: float, segments: int, rings: int, flip: bool = False) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    if flip:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _body_bvh(body: bpy.types.Object) -> BVHTree:
    mesh = body.data
    mesh.calc_loop_triangles()
    verts = [v.co.copy() for v in mesh.vertices]
    tris = [tuple(lt.vertices) for lt in mesh.loop_triangles]
    return BVHTree.FromPolygons(verts, tris, all_triangles=True, epsilon=0.0)


def _true_signed(bvh: BVHTree, p) -> float:
    """Signed distance to the faceted body sphere: + outside, - inside."""
    co, no, _, _ = bvh.find_nearest(p)
    outward = no if no.dot(co) > 0.0 else -no
    return float((p - co).dot(outward))


def _garment_positions(garment: bpy.types.Object) -> list:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    g_eval = garment.evaluated_get(depsgraph)
    mesh = g_eval.to_mesh()
    try:
        return [g_eval.matrix_world @ v.co for v in mesh.vertices]
    finally:
        g_eval.to_mesh_clear()


def run_case(name: str, depth: float, flip_body: bool) -> bool:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    body = _make_sphere("Body", BODY_RADIUS, 64, 32, flip=flip_body)
    garment = _make_sphere("Garment", BODY_RADIUS - depth, 48, 24)
    bpy.context.view_layer.update()

    bvh = _body_bvh(body)
    before = [_true_signed(bvh, p) for p in _garment_positions(garment)]

    depsgraph = bpy.context.evaluated_depsgraph_get()
    det = cg_detection.detect_clipping(
        garment_obj=garment,
        body_obj=body,
        depsgraph=depsgraph,
        offset_distance=OFFSET,
        detection_radius=DETECTION_RADIUS,
        use_risk_area=False,
    )

    stats = cg_correction.correct_current_pose(
        garment_obj=garment,
        body_obj=body,
        depsgraph=depsgraph,
        offset_distance=OFFSET,
        detection_radius=DETECTION_RADIUS,
        correction_strength=1.0,
        max_push_distance=MAX_PUSH,
        smooth_iterations=0,
        smooth_strength=0.0,
        use_risk_area=False,
        preserve_pinned_areas=False,
    )
    bpy.context.view_layer.update()
    after = [_true_signed(bvh, p) for p in _garment_positions(garment)]

    total = len(after)
    still_inside = sum(1 for s in after if s <= 0.0)
    moved_inward = sum(1 for b, a in zip(before, after) if a < b - 1e-6)
    ok = still_inside == 0 and moved_inward == 0

    # detect_clipping must also see the penetration, with its real depth (also for a flipped body).
    true_depth = -min(before)
    det_pen = det.stats.max_penetration or 0.0
    ok = ok and det.stats.flagged_clipping == total and abs(det_pen - true_depth) < 0.003

    print(f"\n[{name}] depth={depth*100:.1f} cm, offset={OFFSET*100:.1f} cm, body normals flipped={flip_body}")
    print(f"  signed before: min={min(before)*100:+.2f} cm  max={max(before)*100:+.2f} cm")
    print(f"  signed after : min={min(after)*100:+.2f} cm  max={max(after)*100:+.2f} cm")
    print(f"  corrected_verts={stats.corrected_verts}/{total}  still_inside={still_inside}  moved_inward={moved_inward}")
    print(f"  detect_clipping flagged={det.stats.flagged_clipping}/{total}  "
          f"max_penetration={det_pen*100:.2f} cm (true max depth {true_depth*100:.2f} cm)")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> None:
    cases = [
        ("deep", 0.02, False),
        ("deeper_than_detection_radius", 0.03, False),
        ("shallow", 0.005, False),
        ("deep_flipped_body_normals", 0.02, True),
    ]
    results = [run_case(*c) for c in cases]
    passed = sum(results)
    print(f"\n{passed}/{len(results)} cases passed")
    if passed != len(results):
        raise SystemExit(1)


main()
