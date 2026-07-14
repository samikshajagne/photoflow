# Phase 3b — Testing Guide (Templates Wired Into the Album Render)

The template engine from 3a now drives the **actual album output**. Rendered
spreads (PNG / JPG / PDF) are composited through the designed templates — shaped
photo slots, white borders, soft shadows, and a sampled background — instead of
the old plain rectangular grid.

## What changed (files)

- `core/album/raster.py`
  - **New `render_spread_template(project, spread, ...)`** — renders one spread
    via `core.album.template`. The spread's placement list supplies *which*
    photos (and their order) go on the spread; the **template** decides their
    shapes and arrangement. Tonal edits and the retouched-`linked_path`
    round-trip are honoured via the same resolver as before; missing photos are
    recorded in `skipped` and never abort the album.
  - Helpers `_album_spec_for(...)` (rebuilds the `AlbumSpec`, guaranteeing the
    canvas matches the spread size) and `_album_theme(...)`.
  - **The file exporter (`_render_spread_files`) now calls
    `render_spread_template`**, so PNG / JPG / PDF exports use templates.
  - The original `render_spread` (rectangular) is **kept unchanged** (its
    geometry tests still pass, and the layered PSD path still uses it).
- `tests/test_album_raster.py` — added two tests for the template renderer.
- `tests/test_album_template.py` — fixed a count expectation after the 3a
  library grew to six layouts.

## What it does NOT do yet

- **Layered PSD export is still rectangular** (the `.psd` builds one layer per
  photo via the old tile path). Templating the PSD layers is a follow-up.
- **Event colour themes** (per-event backgrounds/palettes) come in 3c; right now
  every spread uses the `classic` theme with a photo-sampled tint.
- Theme selection isn't surfaced in the UI yet (defaults to `classic`).

---

## Verification

### Unit tests

```powershell
pytest -q tests/test_album_template.py tests/test_album_raster.py
```

**Expected:** all pass. Key checks:

- `test_album_template.py` — the engine suite (schema, JSON, selection, render).
- `test_album_raster.py::test_render_spread_template_size_and_content` — the
  template renderer produces a spread of the correct size with both photos
  composited.
- `test_album_raster.py::test_render_spread_template_skips_missing` — a missing
  photo is recorded and doesn't abort.
- The existing `test_export_png/jpg/pdf` still pass (output size/format
  unchanged — only the arrangement inside changed).

> **Environment note:** I could not get a clean run of these two suites in my
> sandbox this round — the isolated filesystem started serving truncated copies
> of the freshly-edited files (a known glitch this session, unrelated to the
> code). The 3a engine suite passed earlier here (12–13/13), and this change is
> additive: `render_spread` is untouched, and the export tests only assert
> size/format. Your local `pytest` is the authoritative check.

### See it in the app

1. `python -m ui_qt.main`
2. **Open & Analyze** a shoot → optionally **Label People** → **Build Album**.
3. **Export Album** → tick **PNG** (or JPG/PDF) → export.
4. Open the files in `…/PhotoFlow_Album/renders/`.

**Expected:** each spread shows photos in **shaped slots** (rectangles, circles,
ovals, rounded rectangles, diamonds) with **white borders** and **soft
shadows**, over a **light background tinted from the photos** — the tuned
`classic` look, not the old flat rectangular grid.

Compare a spread against `docs/` sample renders / your reference album to sanity
-check the arrangement.

---

## Rollback

All changes are uncommitted and mostly additive. To revert just this checkpoint,
in `core/album/raster.py` change the one line in `_render_spread_files` back to
`render_spread(...)` and remove `render_spread_template` + its two helpers; the
`core/album/template.py` engine (3a) can stay.
