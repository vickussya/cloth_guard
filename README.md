# Cloth Guard (Blender Add-on)

Post-animation garment cleanup for Blender: preserve stylized clothing shapes and reduce body clipping without cloth simulation.

## What Cloth Guard does

Cloth Guard is a **post-animation cleanup** tool for animators and character artists.

It helps you:

- Preserve clean stylized garment shapes during animation playback (stabilization).
- Scan an animation for clipping frames.
- Reduce body-through-clothing clipping with non-destructive corrections.
- Detect self-clipping (garment intersecting itself).
- Use a body mask as a final fallback for small hidden intersections.

Cloth Guard is not a magic one-click cloth solver. It does not replace good rigging, weights, or cloth simulation in every case.

## Supported Blender version

- Blender **3.0+**

## Installation

1. On GitHub: `Code` → `Download ZIP`
2. In Blender: `Edit` → `Preferences` → `Add-ons` → `Install...`
3. Select the downloaded ZIP, then enable **Cloth Guard**.

## Best recommended workflow (beginner-friendly)

1. **Save a copy** of your Blender file (keep a backup).
2. Assign the **Body Object**.
3. Add your clothing meshes to **Garments** (select them in the viewport, then click **Add Selected Garment**).
4. Click **Setup Cloth Guard**.
5. Go to a clean frame where the garment looks correct, then click **Store Rest Shape**.
6. Use **Shape Preservation** first if the garment loses its form.
7. Set **Start Frame** and **End Frame**.
8. Click **Scan Animation**.
9. Review the **problem frames** list and jump to them.
10. On a problem frame, run **Detect Clipping**, then **Generate Correction (Current)**.
11. If needed, run **Detect Self-Clipping** to find garment self-intersections.
12. If tiny body intersections remain under the garment, run **Create Body Mask**.
13. Scrub the timeline and review. If the result is too strong, lower settings and regenerate.

## Shape Preservation workflow (stabilization)

Use this when the garment gets ugly wrinkles, collapses, shrinks, or loses its designed silhouette during the shot.

Steps:

1. Go to a clean frame where the garment looks correct.
2. Click **Store Rest Shape**.
3. Go to a problem frame.
4. Click **Analyze Shape Drift**.
5. Click **Preserve Shape (Current)**.
6. If it is too strong, lower **Shape Strength** or **Wrinkle Smooth Strength** and try again.

Notes:

- Shape preservation is meant to remove deformation noise. It should not freeze the garment or force it back to a rest pose.

## Anti-Clipping workflow (body intersections)

Use this when the body intersects through the clothing.

Steps:

1. Go to a frame with visible clipping.
2. Click **Detect Clipping**.
3. Click **Select** to inspect the detected vertices.
4. Click **Generate Correction (Current)**.
5. If small intersections remain under the garment, click **Create Body Mask**.
6. If you need to remove it later, click **Delete Body Mask**.

## Self-Clipping workflow (advanced)

Use this when the garment clips into itself (for example sleeve into torso, collar into shoulder, coat panels intersecting).

Steps:

1. Go to a frame where you suspect self-clipping.
2. Click **Detect Self-Clipping**.
3. Click **Select** to inspect the detected vertices.

Self-clipping cleanup is an advanced feature. It uses the stored rest shape to help detect when parts of the same garment collapse into each other. Complex meshes may still need review, but the tool is designed to reduce garment self-intersections without changing the original mesh.

## Combined workflow (recommended order)

Recommended order:

1. **Shape Preservation** (fix garment form first)
2. **Anti-Clipping** (fix body intersections second)
3. **Body Mask** (hide small remaining intersections last)

## Batch workflow

Once current-frame results look good, you can batch:

- **Preserve Shape (All Flagged Frames)**
- **Generate Corrections (All Flagged Frames)**

Tip: test on a short range first (for example **1–30**) before running batch on a full shot.

## Button guide (what each button does)

- **Setup Cloth Guard**: prepares needed vertex groups/modifiers and validates your setup.
- **Store Rest Shape**: stores the clean garment look used as a reference for shape preservation.
- **Analyze Shape Drift**: checks how much the garment shape has changed from the stored reference.
- **Preserve Shape (Current)**: creates a non-destructive correction to reduce unwanted deformation on the current frame.
- **Preserve Shape (All Flagged Frames)**: generates preservation corrections for frames flagged by Scan Animation.
- **Scan Animation**: scans the selected frame range and lists frames where clipping is detected.
- **Detect Clipping**: finds garment vertices that are likely intersecting or too close to the body.
- **Select**: selects detected vertices so you can confirm the detection.
- **Generate Correction (Current)**: creates a non-destructive anti-clipping correction for the current frame.
- **Generate Corrections (All Flagged Frames)**: creates non-destructive anti-clipping corrections across flagged frames.
- **Detect Self-Clipping**: experimental; finds areas where the garment intersects itself.
- **Select Self-Clipping**: selects the detected self-clipping vertices.
- **Create Body Mask**: hides body areas under the garment to remove small remaining intersections.
- **Delete Body Mask**: removes the Cloth Guard body mask and restores hidden body areas.
- **Clear Live Correction**: resets `CG_LiveCorrection` to match Basis (removes the live anti-clip effect).

## Limitations (important)

- Not a cloth simulation replacement.
- Best results come from decent rigging/weights and clean meshes.
- Extreme poses may still need manual cleanup.
- Shape preservation should not freeze the garment.
- Anti-clipping should reduce clipping, not destroy the silhouette.
- Anti-clipping correction is still being improved; body mask is recommended for small remaining hidden intersections.

## Non-destructive guarantee

Cloth Guard is designed to be non-destructive to mesh topology and vertex order:

- It never adds/removes/reorders vertices or faces.
- It never applies/collapses modifier stacks.
- It only uses deformation layers (vertex groups, shape keys, deformation modifiers) that preserve topology.

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See `LICENSE`.

## Author

- **Vickussya**
