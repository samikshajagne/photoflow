# Phase 2a — Testing Guide (Standalone "Cluster People" Backend)

This is a **backend-only** checkpoint that enables the people-first flow. There
is **no user-visible change yet** — the UI still generates the album the same
way. 2a adds the ability to discover and label people *before* building the
album, which the UI reorder (2b) will use.

## What changed (files)

- `core/album/orchestrator.py`
  - New method **`prepare_people(source, output_dir=None, ...)`** — analyzes the
    folder and discovers person clusters, then saves the manifest + cache, but
    does **not** build events/sections/spreads. Returns an `AlbumProject` ready
    for labelling.
  - Refactored the shared analyze+cluster prefix of `generate()` into a private
    `_prepare()` helper. `generate()` behaves exactly as before.
- `tests/test_album_identity.py` — three new tests for the flow.

## Why it matters

Today, people are only clustered *inside* album generation, so you can only
label them after building the album. `prepare_people()` lets the app cluster
people right after analysis, so the photographer labels them first — and
because analysis/faces/embeddings are all cached (Phase 1a), building the album
afterwards recomputes nothing.

---

## Verification — run the unit suite

From `photoflow/` in your venv:

```powershell
pytest -q tests/test_album_identity.py
```

**Expected:** all pass, including the three new tests:

- `test_prepare_people_discovers_clusters_without_layout` — clusters are found,
  but `sections`/`spreads` are empty and a manifest is written.
- `test_prepare_people_labels_flow_into_generate` — labels set on the prepared
  project survive into a later `generate()` (Couple/Bride/Groom sections appear).
- `test_prepare_then_generate_reuses_embeddings` — building the album after
  `prepare_people` does **not** re-embed faces (shared cache; no recompute).

Also run the full suite to confirm nothing regressed (the `generate()` refactor
is covered by the existing `tests/test_album_orchestrator.py` and the rest of
`tests/test_album_identity.py`):

```powershell
pytest -q
```

**Expected:** all pass.

---

## What's next (2b)

2b wires this into the desktop app: after **Open & Analyze**, the guided flow
will show **Label People** as the first interactive step (before **Build
Album**), using `prepare_people()`. That's the checkpoint where you'll see the
reordered flow in the UI.

## Rollback

All changes are uncommitted. `git diff core/album/orchestrator.py` shows the
refactor; `git checkout -- <file>` reverts.

## Known environment note

As before, the assistant's sandbox couldn't execute the suite this session (a
file-sync quirk there). The new code was verified directly against the source
and mirrors the existing, tested `generate()` flow. Your local `pytest` run is
the authoritative check.
