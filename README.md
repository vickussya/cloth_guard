# Cloth Guard (Blender Add-on)

Cloth Guard helps animators preserve garment shape and reduce body clipping without running cloth simulations.

**Cloth Guard** is a production-friendly, non-simulation toolset for keeping rigged clothing closer to its designed silhouette during character animation, while minimizing (as much as realistically possible) clipping into the body.

## What problem it solves

Rigged garments often look good in a rest pose but clip into the character during posing and animation playback. Cloth simulation is not always practical in production (time, stability, art direction, iteration speed). Cloth Guard provides animator-friendly helpers to:

- Hide body geometry that is under clothing (body masking)
- Detect risky/clipping garment areas
- Apply gentle anti-clip deformation (non-simulation) with optional smoothing
- Bake corrections into corrective shape keys for difficult poses

## Key features (MVP)

- **Multi-garment workflow**: assign a Body mesh and add multiple garment mesh objects
- **Body masking**: generate a vertex-group-driven Mask modifier on the body for hiding regions under the garment
- **Clipping/contact detection**: create/update `CG_Contact` (near-body) and `CG_Clipping` (likely penetration) vertex groups per garment
- **Post-animation cleanup**: scan a frame range to find problem frames, then jump to and fix only those frames
- **Non-destructive correction**: generates/updates a live corrective shape key (`CG_LiveCorrection`) instead of modifying the base mesh
- **Risk/control groups**: optional vertex groups (Risk, Pinned, Preserve Collar/Hem/Seams)
- **Corrective shape keys**: bake the current corrected state into a named shape key; optional bone-rotation driver linking (MVP)
- **Per-frame baking**: bake non-destructive per-frame correctives (shape keys) for current frame or for all flagged frames
- **Live anti-clip toggle**: enable/disable `CG_LiveCorrection` quickly while reviewing

## Supported Blender version

- **Blender 3.0+** (minimum)

## Repository layout

This repository is structured so the GitHub **Code -> Download ZIP** archive can be installed directly in Blender.

## Installation

1. On GitHub: `Code` -> `Download ZIP`
2. In Blender: `Edit` -> `Preferences` -> `Add-ons` -> `Install...`
3. Select the downloaded ZIP, then enable **Cloth Guard**.

## Quick start workflow

1. Select your **Body** and **Garment** mesh objects.
2. Open the 3D Viewport sidebar: `N` -> **Cloth Guard** tab.
3. Assign **Body Object** and add garments to the **Garments** list (select garments in the viewport, then click **+**).
4. Click **Setup Cloth Guard**.
5. Click **Create Body Mask** to hide body parts under the garment.
6. (Optional) Click **Detect Clipping** to visualize `CG_Contact` / `CG_Clipping` on the current frame.
7. Use **Post-Animation Cleanup**:
   - Set Start/End/Step and click **Scan Animation**
   - Select a flagged frame and click **Go To Problem Frame**
   - Click **Update Live Corrective** to preview a non-destructive fix (`CG_LiveCorrection`)
   - Click **Generate Correction (Current)** or **Generate Corrections (All Flagged)** to bake per-frame shape keys
8. For artist-authored fixes, click **Create Corrective Shape Key From Current Pose**, name it, and optionally link it to a bone rotation.

## Body masking (what it does)

Body masking helps reduce visible "body poking through cloth" by hiding body geometry that is sufficiently close to the garment. Cloth Guard:

- Creates/updates a body vertex group (`CG_BodyMask`)
- Creates/updates a **Mask** modifier on the body and **inverts** it so the vertex group represents hidden regions

This is a visibility/cleanup workflow - it does not physically push the cloth away, but it often removes the most distracting clipping artifacts.

## Current-pose correction (what it does)

Cloth Guard's MVP anti-clip correction is intentionally **non-simulation**:

- It computes contact/clipping weights (`CG_Contact` / `CG_Clipping`) using closest-surface distance plus a penetration heuristic
- It writes deltas into a live shape key (`CG_LiveCorrection`) so the correction is **non-destructive** and can be disabled instantly

This aims for **small, predictable fixes** rather than aggressive "perfect collisions".

## Corrective shape keys (what they do)

For extreme poses (raised arms, torso twists), automated anti-clip deformation may not be enough. Cloth Guard supports baking the current corrected state into a named shape key, so artists can:

- Keep a stable designed silhouette
- Author pose-specific fixes
- Drive correctives from bone rotation ranges (MVP)

## Limitations (important)

- Cloth Guard is **not** a physics cloth simulation replacement.
- It will not produce perfect collision in all scenarios (dense layering, extreme deformation, very thin offsets).
- Extreme poses may still require manual corrective keys and/or careful weight painting of control groups.
- Best results are typically achieved with stylized or art-directed garments where you want controlled shapes.
- **Two correction modes**:
  - **Shape-key correction** (best when topology is stable): used when evaluated vertex count matches the base mesh.
  - **Helper/modifier correction** (for live modifier stacks): used automatically when topology-changing modifiers are present (Subdivision/Geometry Nodes/etc.). This keeps the stack non-destructive and animator-friendly.

## Non-destructive guarantee

Cloth Guard is designed to be **non-destructive to mesh topology and vertex order**:

- It never adds/removes/reorders vertices or faces.
- It never applies/collapses modifier stacks.
- It only uses deformation layers: vertex groups, shape keys, and deformation modifiers (e.g. Shrinkwrap / smoothing) that preserve topology.

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See `LICENSE`.

## Author

- **Vickussya**
