# Cloth Guard - Repository Notes

This repository keeps the Blender add-on source files in the add-on package folder `cloth_guard/`.

Guidelines:

- Minimum Blender version: **3.0+**
- License: **GPL-3.0** (keep GPL notice headers in source files)
- Keep operator ids under the `cloth_guard.*` namespace
- Prefer small, predictable, animator-friendly tools (not cloth simulation)

Repository conventions:

- Add-on modules live in `cloth_guard/` (e.g. `__init__.py`, `operators.py`, `panels.py`, `properties.py`, `utils.py`).
- Do not commit build outputs (zips, staging folders, `dist/`). These should stay ignored via `.gitignore`.
- Installation for testing: zip the `cloth_guard/` folder (so the zip contains a single top-level `cloth_guard/` folder) or copy `cloth_guard/` into Blender's add-ons directory.
