"""
Cloth Guard add-on packager.

GitHub "Download ZIP" wraps the repo in an extra folder (often with a dash),
which Blender may not load as an add-on package folder name. This script builds
a clean Blender-installable archive with a single top-level add-on folder:

cloth_guard.zip
  cloth_guard/
    __init__.py
    operators.py
    panels.py
    properties.py
    utils.py
"""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path


DEFAULT_DIST_DIR = "dist"
DEFAULT_ZIP_NAME = "cloth_guard.zip"

ADDON_FOLDER_IN_ZIP = "cloth_guard"
ADDON_PACKAGE_DIR = "cloth_guard"


def _is_ignored_path(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    if "__pycache__" in parts:
        return True
    if any(p.endswith(".pyc") for p in parts):
        return True
    return False


def build_zip(*, repo_root: Path, output_zip: Path) -> None:
    addon_dir = repo_root / ADDON_PACKAGE_DIR
    if not addon_dir.is_dir():
        raise SystemExit(f"Missing add-on folder: {addon_dir}")
    if not (addon_dir / "__init__.py").is_file():
        raise SystemExit(f"Missing add-on entry file: {addon_dir / '__init__.py'}")

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()

    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(addon_dir.rglob("*")):
            if path.is_dir():
                continue
            if _is_ignored_path(path):
                continue
            arcname = path.relative_to(repo_root).as_posix()
            zf.write(path, arcname)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an installable Blender add-on zip for Cloth Guard.")
    parser.add_argument(
        "--output",
        default=str(Path(DEFAULT_DIST_DIR) / DEFAULT_ZIP_NAME),
        help="Output zip path (default: dist/cloth_guard.zip).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the dist folder before building.",
    )

    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent
    output_zip = (repo_root / args.output).resolve()

    if args.clean:
        dist_dir = (repo_root / DEFAULT_DIST_DIR).resolve()
        if dist_dir.is_dir():
            shutil.rmtree(dist_dir)

    build_zip(repo_root=repo_root, output_zip=output_zip)
    print(f"Built: {output_zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
