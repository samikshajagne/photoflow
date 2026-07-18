# Phase 5a — Testing Guide (Brush Cutouts + Script Font)

Two of your sample album's signature touches: photos with a **painterly
torn/brush edge** (not just clean shapes), and an **elegant script** for the
cover flourish. Both are self-contained — no purchased assets needed.

## What changed (files)

- `core/album/brushmask.py` — **new**. `brush_mask(size, seed)` generates a
  procedural rough, feathered "torn paper / brush stroke" alpha mask (a
  noise-perturbed, feathered boundary). Deterministic per `seed`.
- `core/album/template.py` — new slot shape **`SHAPE_BRUSH = "brush"`**, wired
  into the renderer (feathered edge, no hard border, per-slot seed). The
  built-in theme now uses it for the **single-photo spread** and the **hero of
  the 4-photo spread**.
- `core/album/textlayer.py` — a **`script`** font role (used for the cover
  subtitle) plus upgraded elegant serifs. Prefers a drop-in
  `data/fonts/Script.ttf`; otherwise falls back to the bundled **Lora Italic**.
- `data/fonts/` — added redistributable **Lora** (regular + italic, OFL) for
  nicer serif/italic text.
- Tests: `tests/test_album_brushmask.py` (new, 5); brush-render test in
  `tests/test_album_template.py`; script-fallback test in
  `tests/test_album_textlayer.py`.

## Verification

### Unit tests

```powershell
pytest -q tests/test_album_brushmask.py tests/test_album_template.py tests/test_album_textlayer.py tests/test_album_raster.py
```

**Expected:** all pass. Highlights:

- `test_album_brushmask.py` — mask size/mode, a feathered (partial-alpha) edge,
  opaque interior, deterministic per seed, varies across seeds. *(I ran this
  here: 5/5 passed.)*
- `test_album_template.py::test_brush_shape_renders` — a brush slot renders and
  the photo shows through the torn edge.
- `test_album_textlayer.py::test_resolve_script_font_falls_back_gracefully` —
  the script role resolves even with no `Script.ttf`.

> **Environment note:** as all session, my sandbox couldn't run the modules that
> were edited in place (`template.py`, `textlayer.py`) — it served truncated
> copies (a file-sync glitch, not the code). The **new** `brushmask` module ran
> here (5/5) and the torn edge is confirmed in the attached demo; the template/
> text wiring was verified against the source. Your local `pytest` is
> authoritative.

### See it in the app

1. `python -m ui_qt.main`
2. **Open & Analyze** → **Build Album** (add couple names for the cover) →
   **Export**.
3. Open the renders.

**Expected:** single-photo spreads and the 4-photo hero show a **soft torn/brush
edge** against the tinted background (instead of a hard rectangle), and the
cover subtitle renders in an **elegant script/italic**.

## Enabling true calligraphy

Drop any calligraphy `.ttf` (e.g. Great Vibes, Allura, Dancing Script) into
`data/fonts/` named **`Script.ttf`**. The cover subtitle (and anything using the
`script` role) will pick it up automatically — no code change.

## Tuning knobs

- **Edge character:** `roughness` / `feather` args in
  `core/album/brushmask.py` (bigger = more ragged / softer).
- **Where brush is used:** the `SHAPE_BRUSH` slots in `default_templates()` in
  `core/album/template.py` — add it to more templates for a more painterly album.
- **Script size:** the `short * 0.055` factor for the subtitle in `draw_cover`.

## Rollback

All additive. Remove the `SHAPE_BRUSH` branch/usages in `template.py`, the
`script` role in `textlayer.py`, and delete `core/album/brushmask.py`,
`data/fonts/Lora*.ttf`, and `tests/test_album_brushmask.py`.
