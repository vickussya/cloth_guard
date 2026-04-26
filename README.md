# Cloth Guard (Blender Add-on)

Post-animation garment cleanup for Blender: preserve stylized clothing shapes and reduce body clipping without cloth simulation.

## What Cloth Guard Does

Cloth Guard is a post-animation cleanup tool for animators and character artists.

It helps with two common problems:

1. **Shape Preservation / Stabilization**  
   Keeps stylized garments looking clean during animation by reducing ugly deformation noise (pinching, collapsing, messy wrinkles).

2. **Anti-Clipping / Post-Animation Cleanup**  
   Reduces body-through-clothing intersections and gives you tools to hide small remaining intersections.

Cloth Guard is not a magic one-click cloth solver. It does not replace proper rigging, weight painting, or cloth simulation in every case.

## Supported Blender version

- Blender **3.0+**

## Installation

1. On GitHub: `Code` -> `Download ZIP`
2. In Blender: `Edit` -> `Preferences` -> `Add-ons` -> `Install...`
3. Select the downloaded ZIP, then enable **Cloth Guard**.

## Recommended Basic Workflow

1. **Save a copy** of your Blender file (always keep a backup).
2. Assign **Body Object**.
3. Add one or more **Garment Objects** (select garments in the viewport, then click **Add Selected Garment(s)**).
4. Click **Setup Cloth Guard**.
5. Go to a clean frame where the garment looks correct and click **Store Rest Shape**.
6. Set **Start Frame** and **End Frame**.
7. Click **Scan Animation**.
8. Review the **problem frames** list and jump to them.
9. Use **Shape Preservation first** if the garment loses its form.
10. Use **Anti-Clipping** after shape preservation if the body intersects the clothing.
11. Scrub the timeline and review. If the result is too strong, lower settings and regenerate.

## Shape Preservation Workflow (Garment Stabilization)

Use this when the garment gets ugly wrinkles, collapses, shrinks, or loses its designed silhouette.

1. Go to a clean frame where the garment looks correct.
2. Click **Store Rest Shape**.
3. Go to a problem frame.
4. Click **Analyze Shape Drift**.
5. Click **Preserve Shape (Current)**.
6. Check if the garment keeps its form better.
7. If it overcorrects, lower **Shape Strength** or **Wrinkle Smooth Strength** and try again.

Notes:

- Shape preservation is meant to remove deformation noise. It should not freeze the garment or force it back to a rest pose.

## Anti-Clipping Workflow (Body Intersections)

Use this when the body intersects through the clothing.

1. Go to a frame with visible clipping.
2. Click **Detect Clipping**.
3. Click **Select** to inspect the detected vertices.
4. Click **Generate Correction (Current)**.
5. Check if clipping is reduced.
6. If small body intersections remain under the garment, click **Create Body Mask**.
7. If you need to remove it, click **Delete Body Mask**.

## Combined Workflow (Recommended Order)

Combined cleanup workflow:

1. Store Rest Shape on a clean frame.
2. Scan Animation.
3. Go to a problem frame.
4. If the garment shape is distorted, run **Preserve Shape (Current)** first.
5. Then run **Detect Clipping**.
6. Use **Select** to check detected clipping areas.
7. Run **Generate Correction (Current)**.
8. If tiny body intersections remain under the garment, run **Create Body Mask**.
9. Scrub the timeline and review the result.
10. If the result is too strong, lower the settings and regenerate.

Why this order:

- Shape preservation cleans the garment form first.
- Anti-clipping fixes remaining body intersections second.
- Body mask hides small remaining body penetration under the garment as a final fallback.

## Batch Workflow

Once current-frame results look good, you can batch:

- **Preserve Shape (All Flagged Frames)**
- **Generate Corrections (All Flagged Frames)**

Tip: test on a short range first (for example **1–30**) before running batch on a full shot.

## Recommended Testing Workflow

- Duplicate the garment and test on the duplicate first.
- Hide the original garment.
- Test on a short frame range.
- Change only one setting at a time.

## Important Limitations

- Not a perfect automatic cloth solver.
- Best results come from clean topology and decent rigging/weights.
- Extreme poses may still need manual corrective edits.
- Shape preservation should not freeze the cloth.
- Anti-clipping should reduce clipping, not destroy the silhouette.

## Non-destructive guarantee

Cloth Guard is designed to be non-destructive to mesh topology and vertex order:

- It never adds/removes/reorders vertices or faces.
- It never applies/collapses modifier stacks.
- It only uses deformation layers (vertex groups, shape keys, deformation modifiers) that preserve topology.

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See `LICENSE`.

## Author

- **Vickussya**
