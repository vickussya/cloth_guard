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
Background test: does a shape-key correction still push OUTWARD after an Armature
modifier re-poses the garment?

Run from the repository root:
    blender -b --factory-startup --python-exit-code 1 --python tests/test_armature_space.py

Scene: a cylinder "arm" (radius 10 cm, along +Z) and a "sleeve" 1 cm inside it, both
skinned 100% to one bone. Case "rest": bone not rotated. Case "raised_90": bone rotated
90 degrees about X, so the arm points along -Y. correct_current_pose writes
CG_LiveCorrection; we then evaluate the posed sleeve and measure, for every vertex,
its distance from the POSED arm axis minus the arm radius (+ outside, - inside).
Case "degenerate": the bone is scaled to 0 on X, which flattens the sleeve so the rig cannot
be undone; the Generate Correction button must warn with the garment, the frame and next steps.
"""

import addon_utils
import importlib
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(REPO_DIR))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.basename(REPO_DIR)
cg_utils = importlib.import_module(MODULE + ".utils")

from _helpers import call_capturing_console  # noqa: E402  (needs the sys.path line above)

ARM_RADIUS = 0.10
SLEEVE_RADIUS = 0.09  # 1 cm inside the arm
OFFSET = 0.005


def _cylinder(name: str, radius: float, depth: float, segments: int, caps: bool, rings: int) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=caps, segments=segments, radius1=radius, radius2=radius, depth=depth,
        matrix=Matrix.Translation((0.0, 0.0, 1.0)),
    )
    if rings > 0:
        vertical = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-6]
        bmesh.ops.subdivide_edges(bm, edges=vertical, cuts=rings, use_grid_fill=False)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _skin_to_bone(obj: bpy.types.Object, rig: bpy.types.Object) -> None:
    vg = obj.vertex_groups.new(name="Bone")
    vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = rig


def _build(raise_deg: float, flatten: bool = False):
    """flatten=True: scale the bone to 0 on X (sleeve becomes a flat strip) and leave the arm unrigged."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable(MODULE, default_set=True, handle_error=None)
    rig_data = bpy.data.armatures.new("Rig")
    rig = bpy.data.objects.new("Rig", rig_data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bone = rig_data.edit_bones.new("Bone")
    bone.head, bone.tail = (0.0, 0.0, 0.0), (0.0, 0.0, 2.0)
    bpy.ops.object.mode_set(mode="OBJECT")

    arm = _cylinder("Arm", ARM_RADIUS, 2.0, 64, caps=True, rings=0)
    sleeve = _cylinder("Sleeve", SLEEVE_RADIUS, 1.6, 48, caps=False, rings=8)
    if not flatten:
        _skin_to_bone(arm, rig)
    _skin_to_bone(sleeve, rig)

    pb = rig.pose.bones["Bone"]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler = (math.radians(raise_deg), 0.0, 0.0)
    if flatten:
        pb.scale = (0.0, 1.0, 1.0)
    bpy.context.view_layer.update()

    head = rig.matrix_world @ pb.head
    axis = ((rig.matrix_world @ pb.tail) - head).normalized()
    return rig, arm, sleeve, head, axis


def _measure(sleeve: bpy.types.Object, head: Vector, axis: Vector):
    """Per evaluated vertex: (radial signed distance to the arm surface, position along the axis)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    s_eval = sleeve.evaluated_get(depsgraph)
    mesh = s_eval.to_mesh()
    try:
        out = []
        for v in mesh.vertices:
            p = s_eval.matrix_world @ v.co - head
            along = p.dot(axis)
            radial = (p - axis * along).length
            out.append((radial - ARM_RADIUS, along))
        return out
    finally:
        s_eval.to_mesh_clear()


def run_case(name: str, raise_deg: float) -> bool:
    rig, arm, sleeve, head, axis = _build(raise_deg)
    before = _measure(sleeve, head, axis)
    stats = cg_utils.correct_current_pose(
        garment_obj=sleeve,
        body_obj=arm,
        depsgraph=bpy.context.evaluated_depsgraph_get(),
        offset_distance=OFFSET,
        detection_radius=0.02,
        correction_strength=1.0,
        max_push_distance=0.03,
        smooth_iterations=0,
        smooth_strength=0.0,
        use_risk_area=False,
        preserve_pinned_areas=False,
    )
    bpy.context.view_layer.update()
    after = _measure(sleeve, head, axis)

    # The two open end rings are hem/cuff borders, which the add-on pushes only partly on purpose
    # (boundary protection), so only the interior vertices are judged.
    edge_faces: dict = {}
    for poly in sleeve.data.polygons:
        for key in poly.edge_keys:
            edge_faces[key] = edge_faces.get(key, 0) + 1
    border = {v for key, n in edge_faces.items() if n == 1 for v in key}
    interior = [i for i in range(len(after)) if i not in border]

    signed = [after[i][0] for i in interior]
    slide = [abs(after[i][1] - before[i][1]) for i in interior]
    inside = sum(1 for s in signed if s <= 0.0)
    ok = inside == 0
    print(f"\n[{name}] bone rotated {raise_deg:.0f} deg, posed arm axis = ({axis.x:+.2f}, {axis.y:+.2f}, {axis.z:+.2f})")
    print(f"  corrected_verts={stats.corrected_verts}/{len(after)}  (judging {len(interior)} interior verts)")
    print(f"  radial signed before: min={min(before[i][0] for i in interior)*100:+.2f} cm")
    print(f"  radial signed after : min={min(signed)*100:+.2f} cm  max={max(signed)*100:+.2f} cm  "
          f"still_inside={inside}/{len(interior)}")
    print(f"  slide along the arm : max={max(slide)*100:.2f} cm  (should be ~0: the push must be radial)")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return ok


def run_degenerate_case() -> bool:
    """A rig that flattens the sleeve cannot be undone: the artist must get a clear warning."""
    rig, arm, sleeve, head, axis = _build(0.0, flatten=True)
    settings = bpy.context.scene.cg_settings
    settings.body_object = arm
    item = settings.garments.add()
    item.object = sleeve
    bpy.context.view_layer.objects.active = sleeve
    frame = bpy.context.scene.frame_current
    result, text = call_capturing_console(lambda: bpy.ops.cloth_guard.generate_correction_current_frame())
    warnings = [ln.strip() for ln in text.splitlines() if ln.startswith("Warning")]
    match = [w for w in warnings if "could not be converted back through the rig" in w]
    ok = (
        "FINISHED" in result
        and len(match) == 1
        and f"{sleeve.name}, frame {frame}:" in match[0]
        and "Next:" in match[0]
    )
    print("\n[degenerate] bone scaled to 0 on X (sleeve flattened), arm not rigged")
    print(f"  operator result: {sorted(result)}")
    print(f"  warning: {match[0] if match else '(none)'}")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> None:
    results = [run_case("rest", 0.0), run_case("raised_90", 90.0), run_degenerate_case()]
    print(f"\n{sum(results)}/{len(results)} cases passed")
    if not all(results):
        raise SystemExit(1)


main()
