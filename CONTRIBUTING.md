# Contributing

Thanks for your interest in contributing to Cloth Guard.

## Development notes

- The repository root is the add-on package: `core/` holds the logic, `operators/` the Blender operators (see `AGENTS.md` for the layout).
- Run the background tests in `tests/` before sending changes.
- Keep changes compatible with Blender **3.0+**.
- Avoid simulation-style features; focus on stable, animator-friendly correction workflows.

## Code style

- Prefer clear naming and modular functions.
- Ensure `register()` / `unregister()` work without import errors.

