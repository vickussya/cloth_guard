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
Background test for Create Coverage Mask (Animation).

Run from the repository root:
    blender -b --factory-startup --python-exit-code 1 --python tests/test_coverage_mask.py

Scene (frames 1-3):
- body = a "leg" (64-sided cylinder, radius 8 cm, z 0..2) + a small "hand" cylinder in the same mesh;
- garment = an 8-sided "sleeve" (corners at 8.2 cm, flat faces at 7.6 cm, z 0.2..1.8): its vertices
  are outside the leg but its faces are inside, so the leg pokes through between sleeve vertices;
- a shape key moves the hand against the sleeve on frame 2 only.
"""

import addon_utils
import importlib
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(REPO_DIR))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.basename(REPO_DIR)

from _helpers import call_capturing_console  # noqa: E402  (needs the sys.path line above)

LEG_RADIUS = 0.08
HAND_FAR_X = 0.6  # hand position on frames 1 and 3
HAND_NEAR_X = 0.105  # hand center on frame 2: its surface is ~3 mm from the sleeve

FAILURES: list = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{('  [' + detail + ']') if detail else ''}")
    if not cond:
        FAILURES.append(label)


def _cylinder(bm, radius, depth, segments, caps, center, cuts):
    geom = bmesh.ops.create_cone(
        bm, cap_ends=caps, segments=segments, radius1=radius, radius2=radius, depth=depth,
        matrix=Matrix.Translation(center),
    )
    verts = geom["verts"]
    vertical = list({e for v in verts for e in v.link_edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-6})
    if cuts:
        bmesh.ops.subdivide_edges(bm, edges=vertical, cuts=cuts, use_grid_fill=False)


def build_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable(MODULE, default_set=True, handle_error=None)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 3

    # Body: leg + hand in one mesh.
    me = bpy.data.meshes.new("Body")
    bm = bmesh.new()
    _cylinder(bm, LEG_RADIUS, 2.0, 64, True, (0.0, 0.0, 1.0), cuts=39)
    _cylinder(bm, 0.02, 0.2, 16, True, (HAND_FAR_X, 0.0, 1.0), cuts=3)
    bm.to_mesh(me)
    bm.free()
    body = bpy.data.objects.new("Body", me)
    scene.collection.objects.link(body)
    hand = {v.index for v in me.vertices if v.co.x > 0.3}
    leg = [v.index for v in me.vertices if v.index not in hand]

    # Hand reaches the sleeve on frame 2 only.
    body.shape_key_add(name="Basis", from_mix=False)
    reach = body.shape_key_add(name="HandReach", from_mix=False)
    for i in hand:
        reach.data[i].co.x -= HAND_FAR_X - HAND_NEAR_X
    for frame, value in ((1, 0.0), (2, 1.0), (3, 0.0)):
        reach.value = value
        reach.keyframe_insert("value", frame=frame)

    # An old file: CG_BodyMask sits BEFORE the Armature modifier (it must end up after it).
    body.modifiers.new("CG_BodyMask", "MASK")
    body.modifiers.new("Armature", "ARMATURE")

    # Garment: 8-sided sleeve.
    gm = bpy.data.meshes.new("Sleeve")
    bm = bmesh.new()
    _cylinder(bm, 0.082, 1.6, 8, False, (0.0, 0.0, 1.0), cuts=15)
    bm.to_mesh(gm)
    bm.free()
    sleeve = bpy.data.objects.new("Sleeve", gm)
    scene.collection.objects.link(sleeve)

    settings = scene.cg_settings
    settings.body_object = body
    item = settings.garments.add()
    item.object = sleeve
    settings.scan_start_frame, settings.scan_end_frame = 1, 3
    settings.coverage_frame_step = 1
    scene.frame_set(1)
    return scene, settings, body, sleeve, leg, hand


def masked_set(body) -> set:
    vg = body.vertex_groups.get("CG_BodyMask")
    if vg is None:
        return set()
    return {v.index for v in body.data.vertices if any(g.group == vg.index and g.weight > 0.5 for g in v.groups)}


def run_op():
    result, text = call_capturing_console(lambda: bpy.ops.cloth_guard.create_coverage_mask())
    info = next((ln.strip() for ln in text.splitlines() if "Coverage mask:" in ln), "")
    errors = [ln.strip() for ln in text.splitlines() if ln.startswith(("Error", "Warning", "Traceback"))]
    return result, info, errors


def main() -> None:
    print(f"\nBlender {bpy.app.version_string}")
    scene, settings, body, sleeve, leg, hand = build_scene()
    co = body.data.vertices
    leg_inside = {i for i in leg if 0.3 < co[i].co.z < 1.7}
    leg_ends = {i for i in leg if co[i].co.z < 0.1 or co[i].co.z > 1.9}

    print("\n== ALL_FRAMES (default)")
    settings.coverage_mode = "ALL_FRAMES"
    result, info, errors = run_op()
    print("  ", info)
    check("operator finished without errors", "FINISHED" in result and not errors, " | ".join(errors))
    masked = masked_set(body)
    check("poke-through area under the sleeve is hidden", leg_inside <= masked,
          f"{len(leg_inside & masked)}/{len(leg_inside)}")
    check("leg ends outside the sleeve stay visible", not (leg_ends & masked), f"{len(leg_ends & masked)} hidden")
    check("hand (covered on frame 2 only) stays visible", not (hand & masked), f"{len(hand & masked)} hidden")
    mods = [m.name for m in body.modifiers]
    check("CG_BodyMask sits after the Armature", mods.index("CG_BodyMask") > mods.index("Armature"), str(mods))
    check("mask modifier is on", body.modifiers["CG_BodyMask"].show_viewport)
    check("frame restored", scene.frame_current == 1, f"frame={scene.frame_current}")

    print("\n== Same again, with the mask modifier already hiding vertices")
    first = masked
    result, info, errors = run_op()
    check("identical result (indices not shifted by the existing mask)", masked_set(body) == first,
          f"{len(masked_set(body) ^ first)} differ")

    print("\n== ANY_FRAME (documents the danger)")
    settings.coverage_mode = "ANY_FRAME"
    result, info, errors = run_op()
    print("  ", info)
    masked = masked_set(body)
    check("ANY_FRAME hides part of the hand (it touched the sleeve once)", len(hand & masked) > 0,
          f"{len(hand & masked)}/{len(hand)} hand vertices hidden")
    check("ANY_FRAME still hides the poke-through area", leg_inside <= masked)

    print("\n== Protect group")
    settings.coverage_mode = "ALL_FRAMES"
    protect = body.vertex_groups.new(name="Protect")
    protected = [i for i in leg_inside if 0.9 < co[i].co.z < 1.1]
    protect.add(protected, 1.0, "REPLACE")
    settings.coverage_protect_group = "Protect"
    result, info, errors = run_op()
    print("  ", info)
    masked = masked_set(body)
    check("protected vertices are never hidden", not (set(protected) & masked), f"{len(protected)} protected")
    check("report mentions the protected vertices", f"{len(protected)} kept visible" in info)
    settings.coverage_protect_group = ""

    print("\n== Error during the computation (core function)")
    cov = importlib.import_module(MODULE + ".core.coverage_mask")
    scene.frame_set(3)
    before_mask = masked_set(body)

    def failing_progress(index):
        if index == 1:
            raise RuntimeError("injected failure")

    try:
        cov.compute_coverage_mask(
            scene=scene, depsgraph=bpy.context.evaluated_depsgraph_get(), body_obj=body, garments=[sleeve],
            frames=[1, 2, 3], outward_distance=0.04, inward_distance=0.06, progress=failing_progress,
        )
        raised = False
    except RuntimeError:
        raised = True
    check("the error reaches the caller", raised)
    check("frame restored after the error", scene.frame_current == 3, f"frame={scene.frame_current}")
    check("mask modifier visibility restored after the error", body.modifiers["CG_BodyMask"].show_viewport)

    print("\n== Error during the operator (garment in Edit Mode)")
    masking = importlib.import_module(MODULE + ".operators.masking")
    real = masking.compute_coverage_mask

    def failing_compute(**kwargs):
        kwargs["progress"] = failing_progress
        return real(**kwargs)

    for o in bpy.context.view_layer.objects:
        o.select_set(o == sleeve)
    bpy.context.view_layer.objects.active = sleeve
    bpy.ops.object.mode_set(mode="EDIT")
    masking.compute_coverage_mask = failing_compute
    try:
        result, info, errors = run_op()
    finally:
        masking.compute_coverage_mask = real
    check("operator reports the error", any("injected failure" in e for e in errors), " | ".join(errors)[:200])
    check("Edit Mode restored", sleeve.mode == "EDIT", f"mode={sleeve.mode}")
    check("frame restored", scene.frame_current == 3, f"frame={scene.frame_current}")
    check("mask modifier visibility restored", body.modifiers["CG_BodyMask"].show_viewport)
    check("existing mask unchanged", masked_set(body) == before_mask)
    bpy.ops.object.mode_set(mode="OBJECT")

    print(f"\n{'PASS' if not FAILURES else 'FAIL'}: {len(FAILURES)} failure(s)")
    for f in FAILURES:
        print("  -", f)
    if FAILURES:
        raise SystemExit(1)


main()
