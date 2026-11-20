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
Background workflow test: press the add-on's buttons (operators) in the README order
and check the results, the same way an artist would use the tool.

Run from the repository root:
    blender -b --factory-startup --python-exit-code 1 --python tests/test_workflows.py

Scene: a UV sphere "body" (radius 1) and a UV sphere "garment" 1 cm inside it.
Every operator report of type WARNING or ERROR (and every Python traceback) counts as a failure.
"""

import addon_utils
import os
import sys

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(REPO_DIR))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.basename(REPO_DIR)

from _helpers import call_capturing_console  # noqa: E402  (needs the sys.path line above)

DEPTH = 0.01  # garment starts 1 cm inside the body

FAILURES: list = []
LAST_OUTPUT = {"text": ""}  # console text of the most recent run_op()


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{('  [' + detail + ']') if detail else ''}")
    if not cond:
        FAILURES.append(label)


def run_op(name: str, **kwargs) -> set:
    """Run bpy.ops.cloth_guard.<name>; any Warning/Error report or Python traceback is a failure."""
    result, text = call_capturing_console(lambda: getattr(bpy.ops.cloth_guard, name)(**kwargs))
    LAST_OUTPUT["text"] = text
    bad = [
        ln.strip()
        for ln in text.splitlines()
        if ln.startswith(("Warning", "Error", "Traceback")) or "Error:" in ln or ("[Cloth Guard]" in ln and "ERROR" in ln)
    ]
    check(f"{name} -> {sorted(result)}", not bad and "FINISHED" in result, " | ".join(bad)[:400])
    return result


def _sphere(name: str, radius: float, segments: int, rings: int) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _signed_distances(body: bpy.types.Object, garment: bpy.types.Object) -> list:
    """Signed distance of every evaluated garment vertex to the body sphere (+ outside)."""
    body.data.calc_loop_triangles()
    bvh = BVHTree.FromPolygons(
        [v.co.copy() for v in body.data.vertices],
        [tuple(t.vertices) for t in body.data.loop_triangles],
        all_triangles=True,
    )
    depsgraph = bpy.context.evaluated_depsgraph_get()
    g_eval = garment.evaluated_get(depsgraph)
    mesh = g_eval.to_mesh()
    try:
        out = []
        for v in mesh.vertices:
            p = g_eval.matrix_world @ v.co
            co, no, _, _ = bvh.find_nearest(p)
            outward = no if no.dot(co) > 0.0 else -no
            out.append(float((p - co).dot(outward)))
        return out
    finally:
        g_eval.to_mesh_clear()


def _summary(vals: list) -> str:
    return f"min={min(vals)*100:+.2f}cm max={max(vals)*100:+.2f}cm inside={sum(1 for s in vals if s <= 0.0)}/{len(vals)}"


def _new_scene(subdivide_garment: bool = False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable(MODULE, default_set=True, handle_error=None)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 3
    body = _sphere("Body", 1.0, 64, 32)
    garment = _sphere("Garment", 1.0 - DEPTH, 48, 24)
    if subdivide_garment:
        garment.modifiers.new("Subdivision", "SUBSURF").levels = 1
    settings = scene.cg_settings
    settings.body_object = body
    settings.debug_logging = True  # run every diagnostic line too (catches broken log calls)
    scene.frame_set(1)  # before selecting: the first frame_set after creating objects resets selection
    for o in bpy.context.view_layer.objects:
        o.select_set(o == garment)
    bpy.context.view_layer.objects.active = garment
    return scene, settings, body, garment


def test_shape_key_workflow() -> None:
    print("\n== Shape-key workflow (topology-stable garment)")
    scene, settings, body, garment = _new_scene()
    target = settings.offset_distance + settings.safety_margin

    run_op("add_selected_garments")
    run_op("setup")
    run_op("store_rest_shape")

    # A user shape key that wobbles the garment on frame 3, so shape preservation has drift to fix.
    garment.shape_key_add(name="UserWobble", from_mix=False)
    wobble = garment.data.shape_keys.key_blocks["UserWobble"]
    for i, d in enumerate(wobble.data):
        d.co = d.co + d.co.normalized() * (0.002 if i % 2 else -0.002)
    for frame, value in ((1, 0.0), (2, 0.0), (3, 1.0)):
        wobble.value = value
        wobble.keyframe_insert("value", frame=frame)
    scene.frame_set(1)

    original = _signed_distances(body, garment)
    run_op("detect_clipping")

    # --- Generate Correction (Current), frame 1
    run_op("generate_correction_current_frame")
    after = _signed_distances(body, garment)
    print("   frame 1 after Generate Correction (Current):", _summary(after))
    check("frame 1: every vertex outside the body", min(after) > 0.0)
    check("frame 1: correction applied once, not twice (max < 1.5 x target)", max(after) < 1.5 * target,
          f"target={target*100:.2f}cm")
    keys = garment.data.shape_keys
    live = keys.key_blocks.get("CG_LiveCorrection") if keys is not None else None
    check("CG_LiveCorrection exists", live is not None)
    if live is None:
        return
    basis = keys.key_blocks[0]
    live_left = max((live.data[i].co - basis.data[i].co).length for i in range(len(basis.data)))
    check("live key is empty after baking (the per-frame key holds the correction)", live_left < 1e-9,
          f"max live offset={live_left:.6f}")

    # Per-frame keys use CONSTANT interpolation: halfway to frame 2 the frame-1 key must still be fully on.
    scene.frame_set(1, subframe=0.5)
    frame_key = keys.key_blocks.get("CG_Frame_0001")
    check("CG_Frame_0001 holds 1.0 at frame 1.5 (CONSTANT keyframes)",
          frame_key is not None and abs(frame_key.value - 1.0) < 1e-6,
          f"value={getattr(frame_key, 'value', None)}")

    scene.frame_set(2)
    frame2 = _signed_distances(body, garment)
    check("frame 2 untouched by the frame-1 key", max(abs(a - b) for a, b in zip(frame2, original)) < 1e-6)

    # --- Scan + Generate Corrections (All Flagged Frames)
    settings.scan_start_frame, settings.scan_end_frame, settings.scan_frame_step = 1, 3, 1
    scene.frame_set(2)
    run_op("scan_animation")
    check("scan puts the artist back on their frame", scene.frame_current == 2, f"frame={scene.frame_current}")
    flagged = sorted(int(it.frame) for it in settings.problem_frames)
    check("scan flags frames 2 and 3 (frame 1 is already fixed)", flagged == [2, 3], f"flagged={flagged}")
    run_op("generate_corrections_flagged_frames")
    check("batch puts the artist back on their frame", scene.frame_current == 2, f"frame={scene.frame_current}")
    for frame in (1, 2, 3):
        scene.frame_set(frame)
        s = _signed_distances(body, garment)
        print(f"   frame {frame} after batch:", _summary(s))
        check(f"frame {frame}: every vertex outside, applied once", min(s) > 0.0 and max(s) < 1.5 * target + 0.002)

    # --- Live Anti-Clip checkbox drives the live key value
    scene.frame_set(2)
    run_op("correct_current_pose")
    settings.enable_live_anti_clip = False
    check("Enable Live Anti-Clip OFF sets CG_LiveCorrection to 0", live.value == 0.0, f"value={live.value}")
    settings.enable_live_anti_clip = True
    check("Enable Live Anti-Clip ON sets CG_LiveCorrection to 1", live.value == 1.0, f"value={live.value}")
    run_op("clear_live_correction")

    # --- Shape preservation
    scene.frame_set(3)
    run_op("analyze_shape_drift")
    run_op("generate_shape_preservation_current")
    run_op("generate_shape_preservation_flagged")

    # --- Corrective shape key (applied once, live key emptied)
    # Frame 5 has no per-frame key yet (the frame keys are 0 after their frame), so it still clips.
    scene.frame_set(5)
    check("frame 5 still clips before the corrective", min(_signed_distances(body, garment)) < 0.0)
    bpy.context.view_layer.objects.active = garment
    settings.corrective_name = "CG_TestCorrective"
    run_op("create_corrective_shapekey")
    s = _signed_distances(body, garment)
    check("corrective shape key applied once", max(s) < 1.5 * target + 0.002, _summary(s))

    leftover = [kb.name for kb in garment.data.shape_keys.key_blocks if kb.name.startswith("CG__")]
    check("no temporary CG__ shape keys left behind", not leftover, f"left={leftover}")

    # --- Remaining tools
    run_op("detect_self_clipping")
    run_op("create_body_mask")
    run_op("delete_body_mask")
    run_op("check_garment_compatibility")


def test_helper_workflow() -> None:
    print("\n== Helper/modifier workflow (garment with Subdivision = topology-changing)")
    scene, settings, body, garment = _new_scene(subdivide_garment=True)
    run_op("add_selected_garments")
    run_op("setup")
    before = _signed_distances(body, garment)
    run_op("generate_correction_current_frame")
    mod = garment.modifiers.get("CG_AntiClip")
    check("CG_AntiClip is a Geometry Nodes modifier", mod is not None and mod.type == "NODES",
          f"type={getattr(mod, 'type', None)}")
    check("CG_AntiClip is the last modifier (post-stack)", mod is not None and garment.modifiers[-1] == mod)
    after = _signed_distances(body, garment)
    print("   before:", _summary(before))
    print("   after: ", _summary(after))
    check("helper mode: every evaluated vertex outside the body", min(after) > 0.0)
    check("helper mode: nothing pushed inward", all(a >= b - 1e-6 for a, b in zip(after, before)))

    # "Never keep a worse correction" must SEE the Geometry Nodes correction it is judging.
    run_op("correct_current_pose")
    line = next((ln for ln in LAST_OUTPUT["text"].splitlines() if "remaining (helper mode)" in ln), "")
    words = line.split()
    remaining = int(words[words.index("remaining") - 1]) if "remaining" in words else -1
    check("helper verification sees the correction (0 remaining)", remaining == 0, line.strip()[:160])
    check("helper verification keeps the push (weights not wiped)", min(_signed_distances(body, garment)) > 0.0)
    check("base mesh vertex count unchanged (48x24 UV sphere = 1106)", len(garment.data.vertices) == 1106)


def main() -> None:
    print(f"\nBlender {bpy.app.version_string}")
    test_shape_key_workflow()
    test_helper_workflow()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'}: {len(FAILURES)} failure(s)")
    for f in FAILURES:
        print("  -", f)
    if FAILURES:
        raise SystemExit(1)


main()
