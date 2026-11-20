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

"""Shared helpers for the background tests (not part of the add-on)."""

import ctypes
import os
import sys
import tempfile

# C runtime, used to flush Blender's own console output before we read it.
_LIBC = ctypes.CDLL("ucrtbase") if os.name == "nt" else ctypes.CDLL(None)


def call_capturing_console(fn):
    """
    Call fn() and return (result, console_text).

    In background mode Blender prints every operator report ("Info: ...", "Warning: ...",
    "Error: ...") to the console. Python cannot intercept self.report() directly, so we
    temporarily redirect the process's stdout/stderr into a file and read it back.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    _LIBC.fflush(None)
    saved_out, saved_err = os.dup(1), os.dup(2)
    with tempfile.TemporaryFile() as tmp:
        os.dup2(tmp.fileno(), 1)
        os.dup2(tmp.fileno(), 2)
        try:
            try:
                result = fn()
            except RuntimeError as e:  # bpy.ops raises RuntimeError when the operator reports an ERROR
                result = {"EXCEPTION"}
                print(f"Error: {str(e).strip()}")
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            _LIBC.fflush(None)
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
            os.close(saved_out)
            os.close(saved_err)
        tmp.seek(0)
        text = tmp.read().decode("utf-8", "replace")
    return result, text
