# Phase 1a — Manual Testing Guide (Caching / Speed Fixes)

This checkpoint removes the duplicate heavy work in the analyze → album flow.
Nothing about the *output* should change — the album, categories, and counts
stay the same. What changes is that the slow work now runs **once** and is
reused.

## What changed (files)

- `core/pipeline.py` — `run()` and `_run_faces()` accept an optional
  `AnalysisCache`; face detections are written to (and reused from) a `"faces"`
  cache namespace.
- `core/album/analysis_records.py` — **new**. One shared helper that turns a
  pipeline result into the classified `PhotoRecord` inventory, plus the shared
  cache-location constants.
- `core/album/orchestrator.py` — `_analyze()` uses the shared helper and passes
  the cache into the pipeline; `_run_identity()` reuses cached faces instead of
  re-detecting; the pipeline and identity stages now share one face detector.
- `ui_qt/workers/analysis_process.py` — the desktop "Analyze Folder" pass now
  writes its results to the shared cache so "Generate Album" can reuse them.
- `tests/test_album_orchestrator.py` — test fake updated for the new `run()`
  signature.

## Expected behavior after the change

1. **Analyze runs the full pipeline once** (as before) and now also writes
   `<your photo folder>/PhotoFlow_Album/.photoflow_cache.json`.
2. **Generate Album reuses that cache** instead of re-running detection — it
   should be dramatically faster than before and must produce the same album.
3. **Face detection happens once per photo**, not three times.
4. **Label People → regenerate** reuses everything cached (quality, faces,
   embeddings, edits); only clustering + layout are recomputed.

---

## Step 1 — Run the unit test suite (fastest confidence check)

From the `photoflow/` folder, in your activated venv:

```powershell
pip install -r requirements-dev.txt
pytest -q
```

**Expected:** all tests pass. Pay special attention to:

- `tests/test_album_orchestrator.py` — including
  `test_cache_avoids_recompute_on_second_run` (proves album gen reuses the cache).
- `tests/test_album_identity.py` — proves identity clustering + embedding-cache
  reuse still works.
- `tests/test_pipeline.py`, `tests/test_pipeline_faces.py`,
  `tests/test_analysis_cache.py`.

If anything fails, copy the failure output back to me — that's the signal to fix
before moving on.

## Step 2 — Confirm the cache is created by Analyze

1. Launch the app: `python -m ui_qt.main`
2. **Open Folder** → pick a test shoot (a few hundred photos is enough).
3. Click **Analyze Folder** and let it finish.
4. In Explorer, confirm this file now exists and is non-empty:
   `<your folder>\PhotoFlow_Album\.photoflow_cache.json`

**Expected:** the file exists after analysis (previously it was only created
during album generation).

## Step 3 — Confirm Generate Album reuses the cache (the speed win)

Right after Step 2, without re-analyzing:

1. Click **Generate Album** and accept the settings.
2. Watch the status bar / log — and time it.

**Expected:**

- Album generation is **much faster** than it used to be (no second detection
  pass).
- The log shows the reuse message rather than a fresh pipeline run. Check
  `logs\photoflow.log` for a line like:

  ```
  Album analysis: reusing cached quality for N photo(s).
  ```

  You should **not** see a fresh `Pipeline starting on '...'` line during album
  generation (that would mean it re-ran analysis).

## Step 4 — Confirm the album output is unchanged

Compare against a build from before this change (or just sanity-check):

- Same section names and per-section photo counts.
- Same BestShots / Duplicates / Blurry / Review distribution.
- Spreads look the same.

**Expected:** identical output. This change is purely about *not repeating
work*, so the result must match.

## Step 5 — Confirm Label People is fast and correct

1. Click **Label People**, name one or two clusters, apply.
2. The album regenerates.

**Expected:**

- Regeneration is fast (no re-detection, no re-embedding).
- Labels are applied and persist.
- `logs\photoflow.log` shows embeddings/quality served from cache, not
  recomputed.

## Step 6 (optional) — Deeper trace with the diagnostic runner

For a full, shareable DEBUG capture:

```powershell
python tools\diagnose.py "D:\path\to\your\photos"
```

Then open `logs\photoflow_debug.log` and confirm the analysis stage is reused
on the second pass and faces are not detected twice.

---

## How to tell it's working, in one line

Analyze a folder, then Generate Album **without re-analyzing**: it should be
near-instant compared to before, produce the same album, and the log should say
it reused cached analysis.

## Rollback

All changes are uncommitted, so `git diff` shows exactly what changed and
`git checkout -- <file>` reverts any single file. The new file
`core/album/analysis_records.py` can be deleted to fully revert.

## Known environment note

The unit suite could not be executed in the assistant's sandbox this session (a
file-sync issue in that environment, unrelated to the code). Every edit was
verified against the source directly. Your local `pytest` run in Step 1 is the
authoritative check.
