# Changelog

## 0.1.1 (Unreleased)

- Added a clear post-animation workflow (scan frames, list problem frames, jump to frames, batch generate fixes).
- Multi-garment support (garment list UI; per-garment processing).
- Improved clipping detection (penetration-aware; adds `CG_Contact` vs `CG_Clipping`; reduced border false positives).
- Non-destructive correction workflow (live shape key + per-frame baking; no Basis edits).
- Support for topology-changing modifier stacks via helper/modifier correction mode (no modifier applying required).
- Body mask improvements (create + delete body mask).
- Multi-pass correction with before/after residual reporting.
- Added Shape Preservation system (store rest/reference, analyze drift, preserve shape current/batch).
- Added evaluated/visual rest shape preservation for topology-changing stacks (post-stack binding workflow).
- Documentation and in-addon tooltips updated; non-destructive topology guarantee documented.
- Fixed install-breaking syntax error in `panels.py`.
- Fixed anti-clip correction pushing penetrating vertices further into the body, and never correcting vertices deeper than the offset or the detection radius.
- Fixed "max penetration" reporting 0 for deep clipping; clipping detection now handles bodies with flipped normals.
- Fixed corrections being applied twice after baking (per-frame keys, corrective shape keys, Preserve Shape batch).
- Corrections on rigged garments are now converted back through the rig, so they still push outward on posed limbs (warns when a deformation cannot be undone).
- Fixed the Geometry Nodes helper mode (topology-changing garments) on Blender 4.x/5.x, and its "never keep worse" check, which could not see the correction it was checking.
- Fixed "Enable Live Anti-Clip" doing nothing, Preserve Shape failing on Blender 4.1+, per-frame baking failing on Blender 5.x, and corrective-smooth binding on Blender 3.0/3.1.
- Scan Animation and the batch tools show progress, reuse the body data per frame (faster) and always return to the original frame.
- Errors are reported in the status bar instead of being hidden; new "Debug Logging" option for console diagnostics.

## 0.1.0

- Initial MVP release of Cloth Guard (body masking, clipping detection, current-pose correction, corrective shape key baking)
