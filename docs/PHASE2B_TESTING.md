# Phase 2b — Manual Testing Guide (People-First Flow)

This checkpoint reorders the guided flow so you **label the people before
building the album**, using the cached clustering from 2a. The guided wizard is
now **four steps**:

> **Open & Analyze → Label People → Build Album → Export**

(This supersedes the three-step flow from Phase 1b — the Label People step is
the new addition.)

## What changed (files)

- `ui_qt/views/wizard_bar.py` — added the **Label People** step between Open &
  Analyze and Build Album.
- `ui_qt/workers/album_workers.py` — new **`PreparePeopleWorker`** runs the
  orchestrator's `prepare_people()` (cluster discovery, no layout) off the GUI
  thread.
- `ui_qt/views/main_window.py`
  - After analysis, the flow advances to **Label People** (not straight to
    album).
  - **Label People** now discovers person clusters on demand (`prepare_people`)
    and opens the labelling panel *before* any album exists; applying labels
    advances to Build Album (it no longer force-rebuilds the album).
  - "Label People" is enabled as soon as the folder is analyzed.
  - Factored the busy-dialog worker runner into `_run_busy_worker` (shared by
    album build and the people pass).
- `tests/test_qt_wizard.py` — updated for the four-step model.

## Expected behavior

- The wizard shows four chips: **Open & Analyze → Label People → Build Album →
  Export**.
- Labelling happens *before* building the album, and building afterwards
  recomputes nothing (faces/embeddings are cached).
- Labelling is **optional** — you can skip straight to Build Album.

---

## Step 1 — Run the unit suite

```powershell
pytest -q
```

**Expected:** all pass. Watch:

- `tests/test_qt_wizard.py` — four-step order; analysis completing advances to
  **people**; building the album advances to **export**.
- `tests/test_album_identity.py` — the 2a `prepare_people` tests.

## Step 2 — Label People appears before Build Album

1. Launch: `python -m ui_qt.main`
2. **Open & Analyze** a test shoot (ideally one with a few recurring faces).
3. When analysis finishes, the wizard's next step is **Label People**.

**Expected:** the guided flow points you to Label People, not Build Album.

## Step 3 — Cluster + label works before any album exists

1. Click **Label People** (wizard button or toolbar).
2. A brief "Finding the people in your photos…" busy dialog appears.
3. The labelling panel opens showing one thumbnail per person (largest groups
   first).
4. Assign a couple of roles (e.g. Bride, Groom) and click **Apply labels**.

**Expected:**

- People are discovered *without* building an album first.
- After Apply, the wizard advances to **Build Album**.
- This works even on a fresh folder you've only analyzed (no prior album).

> **Note:** person clustering needs the InsightFace model installed. If it's
> not, the panel shows "No people detected yet" and the album falls back to the
> time+quality layout — that's expected degradation, not a failure. Use
> `python tools\diagnose.py` to confirm which backends are present.

## Step 4 — Build Album reflects the labels (no recompute)

1. Click **Build Album** and accept the settings.

**Expected:**

- The build is fast (analysis/faces/embeddings all cached from earlier).
- The album is person-aware — sections like **Couple / Bride / Groom / Family**
  appear based on the labels you set.
- `logs\photoflow.log` shows cached reuse, not a fresh detection pass.

## Step 5 — Labelling is optional (skip path)

1. Open & Analyze a folder, then click **Build Album** *without* labelling.

**Expected:** the album still builds (it degrades to the time+quality layout).
Labelling is an enhancement, not a gate.

## Step 6 — Re-labelling still works

1. After building, click **Label People** again, change a label, Apply, then
   **Build Album** again.

**Expected:** the album rebuilds quickly and reflects the updated labels.

---

## How to tell it's working, in one line

After analyzing, the wizard sends you to **Label People** first; naming the
bride/groom there makes the subsequent **Build Album** produce person sections —
with no re-analysis in between.

## Rollback

All changes are uncommitted. `git diff` shows them; `git checkout -- <file>`
reverts any file.

## Known environment note

Same as prior checkpoints: the assistant's sandbox couldn't execute the Qt
suite this session (file-sync quirk there). `album_workers.py` and
`test_qt_wizard.py` compiled cleanly; `main_window.py` and `wizard_bar.py` were
verified directly against the source. Your local `pytest` in Step 1 is the
authoritative check.
