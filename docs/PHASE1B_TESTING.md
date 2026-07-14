# Phase 1b — Manual Testing Guide (Flow Streamlining)

This checkpoint removes redundant clicks from the guided flow. No analysis or
album logic changed — only how the user moves through the steps.

## What changed (files)

- `ui_qt/views/wizard_bar.py` — the guided bar is now **three steps** instead of
  five: **Open & Analyze → Build Album → Export**. The separate "Analyze" and
  the no-op "Review" steps are gone.
- `ui_qt/views/main_window.py`
  - Opening a folder now **flows straight into analysis** (one action).
  - Toolbar: "Open Folder" → **"Open & Analyze"**; "Analyze Folder" →
    **"Re-analyze"** (secondary, for re-running on the already-open folder).
  - `load_folder` still does **no** analysis on its own (plain browsing/refresh
    never runs the pipeline); the auto-analyze happens only when you open a
    folder through the Open & Analyze action.
- Tests updated: `tests/test_qt_wizard.py`, `tests/test_qt_shell.py`.

## Expected behavior

- The wizard shows three chips: **Open & Analyze → Build Album → Export**.
- Choosing a folder immediately starts analysis — you no longer click a second
  "Analyze" button.
- There is no "Review" step to click through.
- Album output and analysis results are unchanged from Phase 1a.

---

## Step 1 — Run the unit test suite

From `photoflow/` in your venv:

```powershell
pytest -q
```

**Expected:** all pass. The updated ones to watch:

- `tests/test_qt_wizard.py` — new 3-step order, `load_folder` stays on the
  "open" step, analysis completing advances to "album", album advances to
  "export".
- `tests/test_qt_shell.py::test_main_window_has_toolbar_actions` — toolbar
  labels are now `Open & Analyze`, `Re-analyze`, `Refresh`.

## Step 2 — One-action Open & Analyze (the main win)

1. Launch: `python -m ui_qt.main`
2. Click **Open & Analyze** (toolbar) or the wizard's **Open & Analyze…** button.
3. Pick a test shoot.

**Expected:**

- The thumbnail grid appears, then analysis **starts on its own** — you do not
  click anything else.
- While analyzing, the wizard button is disabled and the status bar shows
  progress.
- When it finishes, the sidebar counts fill and the wizard advances to
  **Build Album**.

## Step 3 — The guided path has three steps

Walk the wizard end to end:

**Open & Analyze → Build Album → Export**

**Expected:** no "Analyze" step and no "Review" step in between. Each chip
lights up as you complete it.

## Step 4 — Browsing/refresh still doesn't analyze

1. Use **Refresh** on an open folder (or note that just loading a folder's grid
   shows thumbnails).

**Expected:** refreshing re-shows the grid without launching analysis (only the
Open & Analyze action auto-analyzes). No `PhotoFlow_Output` is created by
browsing alone.

## Step 5 — Re-analyze still works for power users

1. After a folder is open, click **Re-analyze** (toolbar).

**Expected:** analysis re-runs on the current folder without reopening the
folder picker.

## Step 6 — Nothing downstream regressed

- **Build Album** and **Export** behave exactly as before.
- **Label People** still works and stays fast (Phase 1a caching).

---

## How to tell it's working, in one line

Pick a folder once and analysis runs automatically; the guided bar shows just
three steps with no Analyze or Review click in between.

## Rollback

All changes are uncommitted. `git diff` shows them; `git checkout -- <file>`
reverts any single file.

## Known environment note

As in Phase 1a, the unit suite could not be executed in the assistant's sandbox
this session (a file-sync quirk in that environment). Edits were verified
directly against the source. Your local `pytest` run in Step 1 is the
authoritative check.
