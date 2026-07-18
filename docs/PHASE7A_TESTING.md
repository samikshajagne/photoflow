# Phase 7a — Testing Guide (In-Window Album Preview + Size Control)

You can now **preview the rendered album inside the main window** before
exporting, and **change the page size** at any point — the preview re-renders
and the chosen size carries into export.

## How it works in the app

Guided flow is now: **Open & Analyze → Label People → Build Album → Preview →
Export**.

- After **Build Album**, click **Preview** (toolbar or the wizard button). The
  center panel switches from the thumbnail grid to a **scrollable list of the
  rendered spreads** — exactly what will export (cover, captions, shapes,
  colours and all), just at a fast lower resolution.
- Click **Change Size** to open the album settings (presets like 12×12 / 10×8,
  custom width×height, single/double-page). Accepting **re-lays and re-renders**
  the preview at the new size, and updates what Export will produce.
- Click **Export Album** when happy.

You can change size **before** previewing (via Build Album settings) and **after**
(via Change Size while previewing) — as many times as you like.

## What changed (files)

- `core/album/raster.py`:
  - `render_spread_template(..., spec=None)` — an optional spec override so a
    spread renders at any canvas size (the preview uses this to render the exact
    same content, fast, at low DPI).
  - `preview_spec(spec)` — a low-DPI copy of a spec (same page size) so preview
    spreads render ~instantly.
- `ui_qt/workers/preview_worker.py` — **new**. Renders spreads to preview images
  off the GUI thread and streams them in as they finish.
- `ui_qt/views/preview_view.py` — **new**. The scrollable preview panel.
- `ui_qt/views/main_window.py`:
  - Center is a `QStackedWidget` toggling between the grid and the preview.
  - New **Preview** and **Change Size** toolbar actions + handlers; re-layout on
    size change; the preview worker lifecycle; returns to the grid on
    open/analyze.
- `ui_qt/views/wizard_bar.py` — added the **Preview** step.
- Tests: `tests/test_preview_render.py` (new); `tests/test_qt_wizard.py` updated
  for the new step.

## Verification

### Unit tests

```powershell
pytest -q tests/test_preview_render.py tests/test_qt_wizard.py tests/test_qt_shell.py
```

**Expected:** all pass:

- `test_preview_render.py` — `preview_spec` lowers DPI while keeping page size;
  the `spec=` override renders at the override's dimensions (not the stored
  ones).
- `test_qt_wizard.py` — step order is now
  `open → people → album → preview → export`; building an album advances to
  **preview**.

> **Environment note:** the preview UI is PyQt6, which my sandbox can't run, and
> it also hit the recurring stale-file glitch — so the Qt wiring is verified by
> code review, not execution. This is a **substantial UI change; please test it
> locally** and tell me anything that misbehaves.

### In the app

1. Build an album, then click **Preview** — the spreads render into the center
   panel one by one.
2. Click **Change Size**, pick a different preset (e.g. 10×8), accept — the
   preview should re-render at the new shape.
3. **Export** — the output should match the previewed size/appearance.

## Things to watch (most likely to need a tweak)

Since I couldn't run it, these are the spots I'd check first:
- Preview renders on a background thread; if the window feels briefly busy when
  you hit **Change Size**, that's the re-layout (header reads for 221 photos)
  running on the UI thread — tell me and I'll move it to the worker.
- If the preview images look too small/large on your screen, the display width
  is a constant (`_DISPLAY_WIDTH` in `preview_view.py`) I can adjust.
- If switching folders mid-preview leaves stale spreads, let me know.

## Rollback

Mostly additive. The `spec=` param and `preview_spec` in `raster.py` are safe to
keep; to remove the UI, revert the `main_window.py` / `wizard_bar.py` additions
and delete `preview_view.py` + `preview_worker.py`.
