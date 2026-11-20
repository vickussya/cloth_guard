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
Background load test: enable the add-on, check every cloth_guard.* operator is
registered, disable it, check everything is gone, then enable/disable once more
(the same thing an artist does with the checkbox in Preferences).

Run from the repository root:
    blender -b --factory-startup --python-exit-code 1 --python tests/test_load.py
"""

import addon_utils
import os
import sys

import bpy

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(REPO_DIR))
MODULE = os.path.basename(REPO_DIR)

# Frozen list: these ids are public API (users' keymaps and scripts), they must never change.
EXPECTED_OPERATORS = (
    "add_selected_garments",
    "analyze_shape_drift",
    "bake_corrections",
    "check_garment_compatibility",
    "clear_live_correction",
    "clear_problem_frames",
    "correct_current_pose",
    "create_body_mask",
    "create_corrective_shapekey",
    "delete_body_mask",
    "detect_clipping",
    "detect_self_clipping",
    "generate_correction_current_frame",
    "generate_corrections_flagged_frames",
    "generate_shape_preservation_current",
    "generate_shape_preservation_flagged",
    "go_to_problem_frame",
    "move_garment",
    "refresh_live_correction",
    "remove_active_garment",
    "remove_setup",
    "scan_animation",
    "select_clipping_vertices",
    "select_self_clipping_vertices",
    "setup",
    "store_rest_shape",
)


def _registered(op_name: str) -> bool:
    try:
        getattr(bpy.ops.cloth_guard, op_name).get_rna_type()
        return True
    except (AttributeError, KeyError):
        return False


def _check(label: str, cond: bool, failures: list) -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


def main() -> None:
    failures: list = []
    print(f"\nBlender {bpy.app.version_string}, add-on module '{MODULE}'")

    for round_no in (1, 2):
        print(f"Round {round_no}: enable")
        mod = addon_utils.enable(MODULE, default_set=True, handle_error=None)
        _check("enable() returned the module", mod is not None, failures)
        missing = [op for op in EXPECTED_OPERATORS if not _registered(op)]
        _check(f"all {len(EXPECTED_OPERATORS)} operators registered (missing: {missing})", not missing, failures)
        _check("Scene.cg_settings exists", hasattr(bpy.types.Scene, "cg_settings"), failures)

        print(f"Round {round_no}: disable")
        addon_utils.disable(MODULE, default_set=True, handle_error=None)
        left = [op for op in EXPECTED_OPERATORS if _registered(op)]
        _check(f"all operators unregistered (left: {left})", not left, failures)
        _check("Scene.cg_settings removed", not hasattr(bpy.types.Scene, "cg_settings"), failures)

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    if failures:
        raise SystemExit(1)


main()
