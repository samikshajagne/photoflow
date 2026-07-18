# Phase 6b — Testing Guide (Orientation-Aware Slots)

Fixes the brutal crops from your page 21 (a portrait squished into a wide slot,
a face lost inside a circle/diamond). The renderer now assigns photos to slots
by **orientation**, so a portrait goes into a tall slot and a landscape into a
wide one.

## What changed (files)

- `core/album/raster.py`:
  - `_photo_aspect(path)` — header-only width/height, EXIF-aware.
  - `_order_by_slot_aspect(paths, template, w, h)` — reorders a spread's photos
    to best match the template's slot shapes: it sorts both photos and slots by
    aspect and pairs them (tallest photo → tallest slot, widest → widest),
    minimizing how much any photo must be cropped.
  - `render_spread_template` calls it right after picking the template, so every
    spread benefits.
- `tests/test_orientation_match.py` — new tests.

This works together with 6a: EXIF makes photos upright, and 6b makes sure an
upright portrait lands in a slot shaped for it.

## Verification

### Unit tests

```powershell
pytest -q tests/test_orientation_match.py
```

**Expected:** all pass:

- `test_photo_aspect_portrait_vs_landscape` — aspect detection direction.
- `test_order_puts_portrait_in_tall_slot` — given a template with a wide slot
  and a tall slot, a portrait+landscape pair is reordered so the portrait fills
  the tall slot and the landscape fills the wide one.
- `test_order_single_photo_unchanged` — no-op for single-slot spreads.

> **Environment note:** my sandbox still couldn't execute the edited
> `raster.py` (stale-file glitch this session). The matching logic is verified
> against source and traced by hand; your local `pytest` is the check.

### The real test — re-run your photos

1. `python -m ui_qt.main` → **Open & Analyze** → **Build Album** → **Export**.
2. Look at spreads that mix portrait and landscape photos (and the shaped
   templates — circle/diamond/rounded).

**Expected:** portrait shots now sit in the tall/upright slots and landscapes in
the wide ones, so faces are kept instead of cropped off. Combined with 6a,
page-21-style spreads should look right: upright photos, sensibly placed.

## Note on very dense spreads

With the 6a page budget, many spreads pack ~8–12 photos, which use a near-square
grid — there the matching effect is smaller (all cells are similar). Matching
has the biggest impact on the designed 2–6 photo shape templates. If you'd like
the dense grids themselves to vary cell orientation per photo, that's a natural
extension we can add.

## Still queued

- **6c — template variety** (rotate layouts so spreads differ).
- **6d — gentler auto-edit** (clamp tonal correction).

## Rollback

Additive. Remove the `_order_by_slot_aspect(...)` call in
`render_spread_template` and the two helper functions.
