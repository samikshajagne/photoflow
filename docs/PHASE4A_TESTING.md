# Phase 4a — Testing Guide (Text Overlays, Quotes, Auto B&W)

Section-opening spreads now get a **title + a curated quote** on a legible plate,
and richer spreads render **one accent photo in black-and-white** — two of the
touches that make a designed album feel finished.

## What changed (files)

- `core/album/textlayer.py` — **new**. Pure Pillow text engine:
  - `resolve_font(role, size)` — loads bundled fonts (→ system → default), so
    text works on any OS with no setup.
  - `title_for_section(name)`, `pick_quote(key)` (deterministic), and a curated
    `QUOTES` library.
  - `draw_caption(image, title, quote, accent)` — draws the title + accent rule
    + quote on a soft translucent plate in the lower-left; scales with
    resolution; always legible over a photo.
- `data/fonts/` — **new**. Bundled redistributable DejaVu serif fonts (regular /
  bold / italic) + a bold sans, so captions render without relying on the user's
  system fonts. Drop nicer script/wedding fonts here later and point
  `textlayer._BUNDLED` at them.
- `core/album/raster.py` — `render_spread_template` now:
  - captions the **section-opening spread** (first spread of each section) with
    its title + a quote, tinted with a theme-derived accent;
  - renders **one accent slot in greyscale** on spreads with 3+ photos (auto
    black-and-white), via `_is_section_opener` and a per-slot counter.
- Tests: `tests/test_album_textlayer.py` (new, 5), plus a section-opener test in
  `tests/test_album_raster.py`.

## Verification

### Unit tests

```powershell
pytest -q tests/test_album_textlayer.py tests/test_album_raster.py
```

**Expected:** all pass. Highlights:

- `test_album_textlayer.py` — font resolution, title/quote helpers, and that
  `draw_caption` keeps the image size while actually drawing pixels. *(I ran
  this here: 5/5 passed.)*
- `test_album_raster.py::test_is_section_opener_flags_first_spread` — only the
  first spread of each section is captioned.

> **Environment note:** as in 3b/3c, my sandbox couldn't run the *raster* suite
> this round (it kept serving a truncated copy of the freshly-edited
> `raster.py`). The `textlayer` module was verified here (5/5) and the caption
> styling was confirmed on a real photo (see the attached demo); the raster
> wiring was verified against the source. Your local `pytest` is authoritative.

### See it in the app

1. `python -m ui_qt.main`
2. **Open & Analyze** → optionally **Label People** → **Build Album** →
   **Export** (PNG/JPG/PDF).
3. Open the spreads in `…/PhotoFlow_Album/renders/`.

**Expected:** the first spread of each section shows a **title** (e.g. `HALDI`)
with a short **quote** on a white plate lower-left, and spreads with three or
more photos show **one photo in black-and-white**.

## Tuning knobs

- **Quotes:** edit the `QUOTES` tuple in `core/album/textlayer.py`.
- **Fonts:** drop `.ttf` files into `data/fonts/` and update `_BUNDLED` (e.g. a
  script font for the quote role).
- **B&W policy:** currently the last slot on 3+ photo spreads; adjust `bw_index`
  in `render_spread_template`.

## Rollback

All additive. Remove the caption/B&W block and `_is_section_opener` in
`core/album/raster.py`; delete `core/album/textlayer.py`, `data/fonts/`, and
`tests/test_album_textlayer.py`.
