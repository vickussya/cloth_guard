# Cloth Guard - Repository Notes

This repository keeps the Blender add-on source files at the repository root (next to `README.md` and `LICENSE`).

Guidelines:

- Minimum Blender version: **3.0+**
- License: **GPL-3.0** (keep GPL notice headers in source files)
- Keep operator ids under the `cloth_guard.*` namespace
- Prefer small, predictable, animator-friendly tools (not cloth simulation)

Repository conventions:

- Add-on modules live at the repo root (e.g. `__init__.py`, `operators.py`, `panels.py`, `properties.py`, `utils.py`).
- Keep operator ids under the `cloth_guard.*` namespace.
