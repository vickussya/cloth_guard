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

## 0.1.0

- Initial MVP release of Cloth Guard (body masking, clipping detection, current-pose correction, corrective shape key baking)
