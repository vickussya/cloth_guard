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

"""Debug logging that only prints when "Debug Logging" is enabled in the Cloth Guard settings."""

from __future__ import annotations


def log_debug(context, *args) -> None:
    """
    Print diagnostics only when "Debug Logging" is enabled in the Cloth Guard settings.

    Callers pass the prefix themselves, e.g. log_debug(context, "[Cloth Guard][Scan]", "frame=3").
    """
    settings = getattr(getattr(context, "scene", None), "cg_settings", None)
    if settings is not None and settings.debug_logging:
        print(*args)
