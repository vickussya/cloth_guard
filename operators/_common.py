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

"""Shared operator helpers: settings, validation, object mode, topology checks, error reporting, detection runner."""

from __future__ import annotations

import traceback
from contextlib import contextmanager

import bpy
from bpy.types import Operator

from ..constants import BODY_SIZE_MAX, BODY_SIZE_MIN, CG_VG_CLIPPING, CG_VG_CONTACT
from ..core.body import BodySurface
from ..core.debug import log_debug
from ..core.detection import detect_clipping
from ..core.mesh_utils import evaluated_world_size, is_mesh_object, write_weights_to_vertex_group


# NOTE: Keep operator ids under the `cloth_guard.*` namespace (AGENTS.md).

_NON_DESTRUCTIVE_MSG = "Cloth Guard is non-destructive: it does not change topology or vertex order."


_TOPOLOGY_MODIFIER_TYPES = {
    # Common topology-changing modifiers
    "SUBSURF",
    "REMESH",
    "SKIN",
    "BOOLEAN",
    "DECIMATE",
    "MULTIRES",
    "SOLIDIFY",
    "SCREW",
    "ARRAY",
    "MIRROR",
    "NODES",  # Geometry Nodes can change topology
    "DYNAMIC_PAINT",
    "OCEAN",
    "PARTICLE_SYSTEM",
    "TRIANGULATE",
    "WELD",
    "BEVEL",
    "EDGE_SPLIT",
}


def _eval_vertex_count(obj: bpy.types.Object, depsgraph) -> int:
    obj_eval = obj.evaluated_get(depsgraph)
    me = obj_eval.to_mesh()
    try:
        return int(len(me.vertices))
    finally:
        obj_eval.to_mesh_clear()


def _base_vertex_count(obj: bpy.types.Object) -> int:
    me = getattr(obj, "data", None)
    return int(len(me.vertices)) if me is not None else 0


def _likely_topology_modifiers(obj: bpy.types.Object) -> list[bpy.types.Modifier]:
    mods = []
    for mod in obj.modifiers:
        if not getattr(mod, "show_viewport", True):
            continue
        if mod.type in _TOPOLOGY_MODIFIER_TYPES:
            mods.append(mod)
    return mods


@contextmanager
def _temporarily_disable_modifiers(obj: bpy.types.Object, mods: list[bpy.types.Modifier]):
    prev = []
    try:
        for mod in mods:
            prev.append((mod, bool(mod.show_viewport), bool(mod.show_render)))
            mod.show_viewport = False
            mod.show_render = False
        yield
    finally:
        for mod, sv, sr in prev:
            try:
                mod.show_viewport = sv
                mod.show_render = sr
            except ReferenceError:
                # The modifier was deleted while it was switched off: nothing left to restore.
                log_debug(bpy.context, "[Cloth Guard][Modifiers] modifier removed before it could be restored")


def _run_detection_and_weights(
    *,
    context,
    settings,
    garment_obj: bpy.types.Object,
    body_obj: bpy.types.Object,
    depsgraph,
    body_surface: BodySurface | None = None,
    offset_distance: float | None = None,
    detection_radius: float | None = None,
    keep_enabled: tuple[str, ...] = (),
    write_weights: bool = True,
) -> tuple[object, int, int]:
    """
    Detect and write CG_Contact/CG_Clipping weights safely, using cage mode if enabled.

    keep_enabled: modifier names that stay on in cage mode (e.g. CG_AntiClip when verifying it).
    write_weights: False only measures. Verification must not rewrite CG_Clipping, because that group
    tells the CG_AntiClip modifier which vertices to push.
    Returns (detection_result, contact_affected, clipping_affected); counts are 0 when not writing.
    """
    if offset_distance is None:
        offset_distance = float(settings.offset_distance)
    if detection_radius is None:
        detection_radius = float(settings.detection_radius)
    offset_distance = max(0.0, float(offset_distance))
    detection_radius = max(offset_distance, float(detection_radius))

    mismatch = _topology_mismatch(garment_obj=garment_obj, depsgraph=depsgraph)
    # If topology differs, weight mapping to the base mesh is only reliable in cage mode,
    # so we force cage mode for detection regardless of the UI toggle.
    if mismatch:
        mods = _likely_topology_modifiers(garment_obj)
    else:
        mods = _likely_topology_modifiers(garment_obj) if settings.ignore_topology_modifiers else []
    mods = [m for m in mods if m.name not in keep_enabled]

    with _temporarily_disable_modifiers(garment_obj, mods):
        if mods:
            context.view_layer.update()
            depsgraph = context.evaluated_depsgraph_get()
        det = detect_clipping(
            garment_obj=garment_obj,
            body_obj=body_obj,
            depsgraph=depsgraph,
            body_surface=body_surface,
            offset_distance=offset_distance,
            detection_radius=detection_radius,
            use_risk_area=settings.use_risk_area,
        )
    if mods:
        # The modifiers are switched back on, but the scene still holds the cage result until it is
        # re-evaluated. Without this, the next _topology_mismatch() check saw "no mismatch" and the
        # helper/modifier mode was never chosen.
        context.view_layer.update()

    if not write_weights:
        return det, 0, 0
    contact_affected = write_weights_to_vertex_group(garment_obj, CG_VG_CONTACT, det.contact_weights)
    clipping_affected = write_weights_to_vertex_group(garment_obj, CG_VG_CLIPPING, det.clipping_weights)
    return det, contact_affected, clipping_affected


def _topology_mismatch(*, garment_obj: bpy.types.Object, depsgraph) -> bool:
    return _base_vertex_count(garment_obj) != _eval_vertex_count(garment_obj, depsgraph)


def _warn_if_unusual_scale(op: Operator, settings, body_obj: bpy.types.Object, depsgraph) -> bool:
    """
    Warn when the body is not a real-size character (all Cloth Guard distances are in metres).

    Example: a Mixamo import at 36 m makes the 5 mm offset about 20x too small, so nothing gets
    corrected. Only warns: rescaling the rig and its animation is the artist's decision.
    Returns True when it warned.
    """
    size = evaluated_world_size(body_obj, depsgraph)
    if BODY_SIZE_MIN <= size <= BODY_SIZE_MAX:
        return False
    op.report(
        {"WARNING"},
        f"Body '{body_obj.name}' is {size:.2f} m tall (largest dimension). Cloth Guard distances are in metres "
        f"and tuned for real-size characters ({BODY_SIZE_MIN:g}-{BODY_SIZE_MAX:g} m): offset "
        f"{settings.offset_distance * 1000:.0f} mm, detection radius {settings.detection_radius * 100:.1f} cm, "
        f"max push {settings.max_push_distance * 100:.1f} cm. Fix: select the rig and meshes, Object > Apply > "
        "Scale (Ctrl+A). If the rig has location keyframes, scale those keyframes by the same factor too. "
        "Cloth Guard does not rescale anything itself.",
    )
    return True


def _report_garment_error(
    op: Operator,
    context,
    garment_obj: bpy.types.Object,
    action: str,
    exc: Exception,
    *,
    frame: int | None = None,
    level: str = "WARNING",
) -> None:
    """
    Report one garment's failure in the status bar; the operator then continues with the others.

    Operators catch a broad Exception per garment on purpose: the Blender calls inside (depsgraph
    evaluation, modifier binds, shape keys on linked/overridden data) can fail in many ways, and one
    bad garment must not cancel the work already done on the others. It is never silent: the artist
    sees the error type and message, and the full traceback is printed with Debug Logging on.
    """
    where = f" @ frame {frame}" if frame is not None else ""
    op.report({level}, f"{garment_obj.name}{where}: {action} failed ({type(exc).__name__}: {exc})")
    log_debug(context, "[Cloth Guard][Error]", traceback.format_exc())


def _settings(context):
    return context.scene.cg_settings


@contextmanager
def _temporary_mode_object(context):
    """
    Ensure Blender is in OBJECT mode for the duration of the context.

    This is required for operations like VertexGroup.add() and many data edits,
    which error when the relevant object is in Edit Mode.
    """
    view_layer = context.view_layer
    prev_active = view_layer.objects.active
    prev_mode = prev_active.mode if prev_active is not None else None

    # mode_set() operates on the active object; if no active object exists,
    # attempt to pick one (first selected) to allow mode change.
    if prev_active is None and context.selected_objects:
        view_layer.objects.active = context.selected_objects[0]
        prev_active = view_layer.objects.active
        prev_mode = prev_active.mode if prev_active is not None else None

    try:
        if prev_active is not None and prev_active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        yield
    finally:
        if prev_active is not None and prev_mode is not None and prev_mode != "OBJECT":
            try:
                view_layer.objects.active = prev_active
                bpy.ops.object.mode_set(mode=prev_mode)
            except (RuntimeError, ReferenceError) as e:
                # mode_set's poll failed or the object was deleted: the work is done, only the
                # artist's previous mode could not be restored.
                log_debug(context, "[Cloth Guard][Mode] could not restore mode", prev_mode, e)


def _garments_from_list(settings):
    garments = []
    for item in settings.garments:
        obj = getattr(item, "object", None)
        if not getattr(item, "enabled", True):
            continue
        if obj is None or obj.type != "MESH":
            continue
        garments.append(obj)
    # Keep stable order based on list order.
    return garments


def _iter_garment_meshes_from_collection(coll):
    if coll is None:
        return []
    objs = []
    for obj in coll.all_objects:
        if obj is not None and obj.type == "MESH":
            objs.append(obj)
    objs.sort(key=lambda o: o.name.lower())
    return objs


def _validate_assigned_meshes(settings):
    body_obj = settings.body_object
    if body_obj is None or not is_mesh_object(body_obj):
        return None

    garments = _garments_from_list(settings)
    if not garments:
        # Backwards-compatible fallback: legacy collection/object fields.
        garments = _iter_garment_meshes_from_collection(getattr(settings, "garment_collection", None))
    if not garments and getattr(settings, "garment_object", None) is not None and is_mesh_object(settings.garment_object):
        garments = [settings.garment_object]

    if not garments:
        return None
    return body_obj, garments


def _validate_garments_only(settings):
    garments = _garments_from_list(settings)
    if not garments:
        garments = _iter_garment_meshes_from_collection(getattr(settings, "garment_collection", None))
    if not garments and getattr(settings, "garment_object", None) is not None and is_mesh_object(settings.garment_object):
        garments = [settings.garment_object]
    if not garments:
        return None
    return garments


def _shapekey_delta_stats(*, garment_obj: bpy.types.Object, shapekey_name: str) -> tuple[int, float]:
    if garment_obj.data.shape_keys is None:
        return 0, 0.0
    kb = garment_obj.data.shape_keys.key_blocks.get(shapekey_name)
    basis = garment_obj.data.shape_keys.key_blocks[0]
    if kb is None or len(kb.data) != len(basis.data):
        return 0, 0.0
    changed = 0
    max_delta = 0.0
    for i in range(len(kb.data)):
        d = kb.data[i].co - basis.data[i].co
        ln = float(d.length)
        if ln > 1e-12:
            changed += 1
            max_delta = max(max_delta, ln)
    return changed, max_delta
