# Phase 4b — Testing Guide (Cover Designer + Names/Date)

The album's **Cover** spread now prints the couple's names, the date, and a
subtitle, collected from two new fields in the Build Album dialog. This closes
the last major gap versus your sample album's front.

## What changed (files)

- `ui_qt/views/album_settings_dialog.py` — two new fields, **Couple names** and
  **Wedding date** (free text), with `cover_title()` / `cover_date()` getters.
  Existing size/quality/density behaviour is unchanged.
- `ui_qt/views/main_window.py` — captures the two fields when you build the
  album, remembers them (so they persist if you reopen the dialog), and passes
  them to the album build.
- `ui_qt/workers/album_workers.py` — `GenerateWorker` forwards the cover text.
- `core/album/orchestrator.py` — accepts `cover_title` / `cover_date` and stores
  them in the project meta so the renderer can read them.
- `core/album/textlayer.py` — new **`draw_cover(image, title, date, subtitle)`**:
  a centred title block (names → accent rule → date → subtitle) on a soft plate.
- `core/album/raster.py` — the **Cover** section's spread is rendered with
  `draw_cover` (names + date + "A Successful Love Story"); other section openers
  keep the smaller title+quote caption. If no names are entered, the cover photo
  is left clean.
- `tests/test_album_textlayer.py` — added a `draw_cover` test.

## Verification

### Unit tests

```powershell
pytest -q tests/test_album_textlayer.py tests/test_qt_album_settings.py
```

**Expected:** all pass, including `test_draw_cover_keeps_size_and_draws`. The
settings-dialog tests still pass (the new fields are additive; `album_spec()`
and `selected_density()` are unchanged).

> **Environment note:** my sandbox couldn't run these this round — it got stuck
> serving a stale copy of the freshly-edited `textlayer.py` (the recurring
> file-sync glitch this session). `draw_cover` is confirmed present in the
> source and mirrors `draw_caption`, which I *did* verify rendering earlier
> (see the 4a caption demo). Your local `pytest` + an app export are the
> authoritative checks.

### See it in the app

1. `python -m ui_qt.main`
2. **Open & Analyze** a shoot → optionally **Label People** → **Build Album**.
3. In the Album Settings dialog, fill **Couple names** (e.g. `Ruchika Weds
   Lukesh`) and **Wedding date** (e.g. `24 February 2024`), then build.
4. **Export** (PNG/JPG/PDF) and open the first spread (the Cover).

**Expected:** the cover photo carries a centred title block — the couple's
names, an accent rule, the date, and "A Successful Love Story" — on a soft
legible plate. Leaving the fields blank produces a clean cover with no text.

## Tuning knobs

- **Subtitle text:** the `subtitle="A Successful Love Story"` argument in
  `render_spread_template` (the Cover branch) in `core/album/raster.py`.
- **Cover typography/position:** `draw_cover` in `core/album/textlayer.py`
  (font sizes are fractions of the spread's short edge; the plate sits at ~58%
  height).
- **Fonts:** drop a script `.ttf` into `data/fonts/` and point
  `textlayer._BUNDLED["title"]` at it for a fancier cover.

## Rollback

All additive. Revert the dialog fields + getters, the `cover_title`/`cover_date`
plumbing in `main_window`/`album_workers`/`orchestrator`, and the Cover branch +
`draw_cover`. Nothing else is affected.
