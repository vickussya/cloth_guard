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

- **Garment setup**: assign a Body mesh and a Garment mesh
- **Body masking**: generate a vertex-group-driven Mask modifier on the body for hiding regions under the garment
- **Clipping detection**: create/update a `CG_Clipping` vertex group on the garment for problematic vertices
- **Current pose correction**: configure an anti-clip modifier stack on the garment (no simulation) driven by proximity weights
- **Risk/control groups**: optional vertex groups (Risk, Pinned, Preserve Collar/Hem/Seams)
- **Corrective shape keys**: bake the current corrected state into a named shape key; optional bone-rotation driver linking (MVP)
- **Bake corrections**: current-pose baking implemented; frame-range baking is intentionally left for a future update
- **Live anti-clip toggle**: enable/disable the anti-clip modifiers and refresh weights when needed

## Supported Blender version

- **Blender 3.0+** (minimum)

## Installation

1. Zip the project folder so the zip contains `cloth_guard/` at the top level (and `LICENSE` alongside it).
2. In Blender: `Edit` → `Preferences` → `Add-ons` → `Install...`
3. Select the zip, then enable **Cloth Guard**.

## Quick start workflow

1. Select your **Body** and **Garment** mesh objects.
2. Open the 3D Viewport sidebar: `N` → **Cloth Guard** tab.
3. Assign **Body Object** and **Garment Object**.
4. Click **Setup Cloth Guard**.
5. Click **Create Body Mask** to hide body parts under the garment.
6. Click **Detect Clipping** to visualize problem regions in `CG_Clipping`.
7. Click **Correct Current Pose** (or **Refresh Live Correction**) to update proximity weights and run the anti-clip modifier stack.
8. For hard poses, click **Create Corrective Shape Key From Current Pose**, name it, and optionally link it to a bone rotation.

## Body masking (what it does)

Body masking helps reduce visible “body poking through cloth” by hiding body geometry that is sufficiently close to the garment. Cloth Guard:

- Creates/updates a body vertex group (`CG_BodyMask`)
- Creates/updates a **Mask** modifier on the body and **inverts** it so the vertex group represents hidden regions

This is a visibility/cleanup workflow — it does not physically push the cloth away, but it often removes the most distracting clipping artifacts.

## Current-pose correction (what it does)

Cloth Guard’s MVP anti-clip correction is intentionally **non-simulation**:

- It computes a proximity-based weight map (`CG_Clipping`) using closest-surface distance to the body
- A Shrinkwrap-based anti-clip modifier stack (plus optional smoothing) pushes only the weighted vertices toward the body surface and offsets them outward

This aims for **small, predictable fixes** rather than aggressive “perfect collisions”.

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

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See `LICENSE`.

## Author

- **Vickussya**
