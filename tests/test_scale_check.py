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
Background test: Setup and Check Garment Compatibility warn about a character that is not real size
(e.g. a Mixamo import at 36 m), and never rescale anything themselves.

Run from the repository root:
    blender -b --factory-startup --python-exit-code 1 --python tests/test_scale_check.py
"""

import addon_utils
import os
import sys

import bmesh
import bpy
from mathutils import Matrix

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(REPO_DIR))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.basename(REPO_DIR)

from _helpers import call_capturing_console  # noqa: E402  (needs the sys.path line above)

FAILURES: list = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{('  [' + detail + ']') if detail else ''}")
    if not cond:
        FAILURES.append(label)


def _cylinder(name: str, radius: float, height: float) -> bpy.types.Object:
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=radius, radius2=radius, depth=height,
                          matrix=Matrix.Translation((0.0, 0.0, height / 2)))
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def run_case(label: str, height: float, object_scale: float, expect_warning: bool) -> None:
    print(f"\n== {label}")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable(MODULE, default_set=True, handle_error=None)
    # Height made from mesh size x object scale, like an imported character with scale on the object.
    body = _cylinder("Body", 0.15 * height / object_scale / 1.8, height / object_scale)
    body.scale = (object_scale,) * 3
    garment = _cylinder("Garment", 0.16 * height / object_scale / 1.8, 0.5 * height / object_scale)
    garment.scale = (object_scale,) * 3
    settings = bpy.context.scene.cg_settings
    settings.body_object = body
    settings.garments.add().object = garment
    bpy.context.view_layer.update()

    for op in ("setup", "check_garment_compatibility"):
        _, text = call_capturing_console(lambda: getattr(bpy.ops.cloth_guard, op)())
        warning = next((ln.strip() for ln in text.splitlines() if ln.startswith("Warning") and " m tall" in ln), "")
        if expect_warning:
            check(f"{op} warns about the scale", f"{height:.2f} m tall" in warning, warning[:150])
            check(f"{op} explains the fix", "Apply > Scale" in warning and "location keyframes" in warning)
        else:
            check(f"{op} does not warn for a real-size character", not warning, warning[:150])
    check("nothing was rescaled", tuple(body.scale) == (object_scale,) * 3 and tuple(garment.scale) == (object_scale,) * 3,
          f"body scale={tuple(round(s, 3) for s in body.scale)}")


def main() -> None:
    print(f"\nBlender {bpy.app.version_string}")
    run_case("Mixamo-style import: 36 m tall (object scale 100)", 36.0, 100.0, expect_warning=True)
    run_case("tiny: 5 cm tall", 0.05, 1.0, expect_warning=True)
    run_case("real size: 1.8 m tall", 1.8, 1.0, expect_warning=False)
    print(f"\n{'PASS' if not FAILURES else 'FAIL'}: {len(FAILURES)} failure(s)")
    if FAILURES:
        raise SystemExit(1)


main()
