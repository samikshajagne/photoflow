# Phase 6d — Testing Guide (Gentler Auto-Edit)

Addresses "the auto-correction does not work good on all the photos." The tonal
auto-edit now nudges photos instead of overhauling them, and leaves
already-good or intentionally-stylised shots alone.

## What changed (file: `core/auto_edit.py`)

1. **Strength factor** — new `strength` (default **0.5**): each correction is
   blended halfway toward "no change", so a computed 1.6× exposure is applied as
   1.3×. `strength=0` = fully off (identity).
2. **Dead-zones** — corrections within a small band of neutral are **skipped
   entirely** (exposure ±12%, gains ±6%, contrast ±8%, straighten ±1°), so
   well-exposed/neutral/level photos are untouched.
3. **Tight clamps** — much narrower than before, so nothing gets blown out,
   crushed, or colour-neutralised:
   - exposure `0.8–1.4` (was `0.5–2.5`)
   - white-balance gains `0.85–1.18` (was `0.5–2.0`)
   - contrast `0.92–1.15` (was `0.8–1.4`)
4. **Straighten** — max rotation dropped to **3°** (was 8°) with a 1° dead-zone,
   so a mis-estimated tilt can't visibly skew a good photo.

The upshot: intentional colour moods (e.g. a warm Haldi frame) are preserved
instead of being pulled to gray, and correctly-exposed photos come out unchanged.

## Verification

### Unit tests

```powershell
pytest -q tests/test_auto_edit_gentle.py tests/test_auto_edit.py
```

**Expected:** all pass:

- `test_strength_zero_is_identity` — with `strength=0`, the recipe is a no-op.
- `test_wellexposed_neutral_is_near_identity` — a good photo is left ~unchanged.
- `test_dark_exposure_is_gentle_not_extreme` — a very dark frame brightens but is
  capped at 1.4× (not 2.5×).
- `test_strong_colour_cast_not_over_neutralized` — a saturated yellow keeps its
  mood (gains stay in the tight band).
- The existing `tests/test_auto_edit.py` still passes (directions/orderings
  unchanged; only magnitudes are gentler).

> **Environment note:** my sandbox couldn't execute the edited `auto_edit.py`
> this round (stale-file glitch). Logic verified against source and by
> hand-computing the expected recipe values; your local `pytest` is the check.

### The real test — re-run your photos

Rebuild + export. Compare against the previous output: correctly-exposed photos
should look natural (not over-brightened), and colourful shots keep their colour.

## Tuning / turning off

- **Strength** lives in `core/auto_edit.py` as `DEFAULT_EDIT_STRENGTH = 0.5`.
  Lower it (e.g. 0.3) for even subtler correction, or raise toward 1.0 for
  stronger. (We can surface this as a slider in the Build Album dialog later.)
- **Off entirely:** the Export dialog's "apply edits" toggle already lets you
  export with no tonal correction at all.

## Rollback

Single file. Revert the clamp constants, the `strength`/dead-zone additions, and
the `_soften` helper in `core/auto_edit.py`.

---

## Phase 6 complete

All four output-quality fixes from your feedback are in:

- **6a** — upright photos (EXIF) + ~20–30 pages (page budget, with a Target
  pages field).
- **6b** — orientation-aware slots (portraits in tall slots).
- **6c** — layout variety across spreads.
- **6d** — gentler auto-edit.

Best next step is to **re-run your 221-photo shoot** and compare against the
88-page output — you should see far fewer pages, upright and better-placed
photos, varied layouts, and more natural colour.
