# Cloth Guard - Repository Notes

This repository keeps the Blender add-on source files at the repository root (next to `README.md` and `LICENSE`).

Guidelines:

- Minimum Blender version: **3.0+**
- License: **GPL-3.0** (keep GPL notice headers in source files)
- Keep operator ids under the `cloth_guard.*` namespace
- Prefer small, predictable, animator-friendly tools (not cloth simulation)
- Prefer non-destructive workflows (never apply/collapse modifier stacks; never change topology/vertex order)

Repository conventions:

- Add-on modules live at the repo root (e.g. `__init__.py`, `operators.py`, `panels.py`, `properties.py`, `utils.py`).
- Keep operator ids under the `cloth_guard.*` namespace.

Workflow notes:

- Cloth Guard has two main systems: **Shape Preservation / Stabilization** and **Anti-Clipping / Cleanup**.
- Shape keys are used only when topology is stable; for topology-changing stacks, use post-stack modifier workflows.

Contribution guardrails:

- Always ask for permission before running `git commit` / `git push` unless the user explicitly asked for it.
- If you need a syntax check and system Python is unavailable, use Blender’s bundled Python to run `py_compile` / `compileall`.
