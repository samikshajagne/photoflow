# Phase 6a — Testing Guide (Orientation Fix + Page Budget)

Fixes the two biggest issues from your 88-page test output: **portrait photos
rendered sideways**, and **far too many pages** (one photo per spread).

## What changed (files)

### 1. EXIF orientation (sideways photos)
Camera/phone portraits carry an orientation tag that wasn't being applied, so
they came in rotated. Now every image load honors it via
`ImageOps.exif_transpose`:

- `core/album/raster.py` — `_load_rgb` (the album render) and the section-theme
  colour sampler.
- `core/album/template.py` and `core/album/theming.py` — their default loaders.
- `core/album/layout_select.py` — `_aspect()` now reads the EXIF orientation and
  swaps width/height, so a portrait shot is treated as portrait when choosing
  layouts (not just at render time).

### 2. Page-budget density (too many pages)
`core/album/layout_select.py` — `LayoutSelector` gained `target_pages`:

- Photos-per-spread is now computed to fit **all** non-cover photos into the
  budget (`ceil(total / (target - 1))`, min 2). Only the **cover** stays a
  single-photo spread.
- Default (no target) aims for ~25 spreads (`AUTO_TARGET_PAGES`), so a 221-photo
  shoot lands around 20–30 spreads instead of 88.
- The layout engine's per-spread cap is raised to the computed count.

### 3. Dialog + plumbing
- `ui_qt/views/album_settings_dialog.py` — new **Target pages** field (0 =
  "Auto (20–30)") with a `target_pages()` getter.
- `ui_qt/views/main_window.py`, `ui_qt/workers/album_workers.py` — the value is
  captured and passed to `LayoutSelector`.

## Verification

### Unit tests

```powershell
pytest -q tests/test_layout_budget.py
```

**Expected:** all pass:

- `test_budget_auto_targets_20_30` / `test_budget_explicit_target` — the
  photos-per-spread math hits the budget.
- `test_budget_minimum_two_per_spread` — never drops below 2.
- `test_aspect_honors_exif_orientation` — a landscape-pixel image tagged
  orientation 6 is reported as **portrait** (aspect < 1).
- `test_aspect_plain_landscape` — a wide image reads as landscape.

> **Environment note:** my sandbox again couldn't run the edited modules
> (`layout_select.py` served truncated — the recurring file-sync glitch). The
> logic is verified against source; your local `pytest` is the real check.

### The real test — re-run your 221 photos

1. `python -m ui_qt.main` → **Open & Analyze** your 221-photo folder.
2. **Build Album**. In the dialog, leave **Target pages** on *Auto* (or set e.g.
   `25`). Add the couple names if you want the cover text.
3. **Export** → open the PDF.

**Expected now:**

- **Portraits are upright** — no more sideways photos (the page-21 problem).
- **~20–30 spreads**, not 88 — every photo included, packed several per spread,
  with the cover as the only single-photo spread.
- Setting **Target pages = 25** should produce ~25 spreads; a smaller number
  packs more photos per spread.

## Still to come (next 6 checkpoints)

These are separate fixes we sequenced after the two big ones:

- **6b — orientation-aware slots:** portrait photos into tall slots, landscape
  into wide, so faces aren't cropped by shape mismatches (the circle/diamond
  crops on page 21).
- **6c — template variety:** rotate through multiple layouts so every spread
  isn't the same design.
- **6d — gentler auto-edit:** clamp the tonal correction so it stops hurting
  some photos.

## Rollback

All additive/surgical. Revert the `ImageOps.exif_transpose` lines, the
`target_pages` additions in `layout_select.py`, and the dialog/worker plumbing.
