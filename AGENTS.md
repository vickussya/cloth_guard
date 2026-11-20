# Cloth Guard - Repository Notes

This repository keeps the Blender add-on source files at the repository root (next to `README.md` and `LICENSE`).

Guidelines:

- Minimum Blender version: **3.0+**
- License: **GPL-3.0** (keep GPL notice headers in source files)
- Keep operator ids under the `cloth_guard.*` namespace
- Prefer small, predictable, animator-friendly tools (not cloth simulation)
- Prefer non-destructive workflows (never apply/collapse modifier stacks; never change topology/vertex order)

Repository conventions:

- The repo root is the add-on package (GitHub "Download ZIP" installs it as-is):
  - `__init__.py`: `bl_info`, `register()` / `unregister()`, and the submodule reload list for F3 > Reload Scripts.
  - `constants.py`: all `CG_*` names (vertex groups, modifiers, shape keys; saved in users' .blend files, never rename) and tuning constants.
  - `properties.py`, `panels.py`: settings and UI.
  - `core/`: pure logic, no Operator/Panel classes. `debug`, `mesh_utils`, `body` (body BVH per frame), `coverage_mask` (animation body mask), `shape_keys` (live keys, rest-space conversion), `detection`, `correction`, `shape_preservation`, `modifiers`, `drivers`.
  - `operators/`: Operator classes by feature (`garments`, `setup`, `masking`, `shape_preservation`, `detection`, `correction`, `animation`) plus private helpers (`_common`, `_keyframes`, `_passes`). `operators/__init__.py` exposes `CLASSES` in registration order.
  - `tests/`: Blender background tests (`blender -b --factory-startup --python-exit-code 1 --python tests/<test>.py`); excluded from the ZIP via `.gitattributes`.
- Relative imports only. `core/` must never import from `operators/`, `panels.py` or `properties.py`.
- A new submodule must be added to `_SUBMODULES` in `__init__.py` (after everything it imports), or Reload Scripts keeps its old code.
- Every source file starts with the GPL header and a short module docstring.
- Keep operator ids under the `cloth_guard.*` namespace.

Workflow notes:

- Cloth Guard has two main systems: **Shape Preservation / Stabilization** and **Anti-Clipping / Cleanup**.
- Shape keys are used only when topology is stable; for topology-changing stacks, use post-stack modifier workflows.

Contribution guardrails:

- Always ask for permission before running `git commit` / `git push` unless the user explicitly asked for it.
- If you need a syntax check and system Python is unavailable, use Blender’s bundled Python to run `py_compile` / `compileall`.
