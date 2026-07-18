# Phase 6c — Testing Guide (Template Variety)

Fixes "the album follows only one theme/design on all sheets." Consecutive
spreads now rotate through different layouts instead of repeating the same one.

## What changed (files)

- `core/album/template.py`:
  - `select_template(..., variant)` — when several templates match a photo count
    it rotates between them by `variant`, so same-size spreads differ.
  - Added shape-template variants: **`classic-3b`** (three tall panels in a row)
    and **`classic-4b`** (a 2×2 of mixed shapes), alongside the originals.
  - `auto_grid_template(count, theme, variant)` — dense spreads now come in
    **three grid variants** (different column counts; one uses rounded corners),
    so the many-photo spreads (from the 6a page budget) also vary.
- `core/album/raster.py` — `render_spread_template` passes the **spread index**
  as the variant, so layout changes spread to spread.
- Tests: `tests/test_template_variety.py` (new); updated the count assertion in
  `tests/test_album_template.py` for the added variants.

## Verification

### Unit tests

```powershell
pytest -q tests/test_template_variety.py tests/test_album_template.py
```

**Expected:** all pass:

- `test_grid_variants_differ` — three distinct 9-photo grids.
- `test_select_rotates_shape_variants_for_count_3` — variant 0 → `classic-3`,
  variant 1 → `classic-3b`, variant 2 wraps back.
- `test_count_4_has_two_variants`, `test_dense_fallback_grid_varies_by_variant`.

> **Environment note:** my sandbox couldn't execute the edited `template.py`
> this round (stale-file glitch); the variant logic is verified against source.
> Your local `pytest` is the check.

### The real test — re-run your photos

1. Rebuild + export the album.
2. Flip through consecutive spreads.

**Expected:** neighbouring spreads use **different arrangements** (column counts,
shape mixes, hero-vs-grid) rather than the identical layout every time. Dense
spreads alternate between grid variants; 3- and 4-photo spreads alternate
between their two designs.

## Note on "one theme"

This adds *layout* variety. There's still a single colour/decoration **theme**
family (`classic`) — multiple named themes (e.g. a floral vs. minimal set) and
the art-asset layer are a larger, separate effort we can pick up later.

## Still queued

- **6d — gentler auto-edit:** clamp the tonal auto-correction so it stops
  hurting some photos (your remaining complaint).

## Rollback

Additive. Revert the `variant` params in `select_template` /
`auto_grid_template`, remove `classic-3b` / `classic-4b`, and drop the `variant=`
argument in `render_spread_template`.
