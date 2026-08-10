# Phase 3 & 4 — Implementation Log (Session 1)

Companion to `PHASE_3_4_GAP_ANALYSIS.md`. Records what was actually built and
tested against the real `core/album/` code, versus what remains.

## Guiding principle

The roadmap specifies many brand-new files that duplicate existing, working code
(`layout.py` already crops face-safely, `template.py` already renders circle/oval/
diamond frames, `theming.py` already samples mood colour). So this session
**completed genuine gaps and reused existing modules** rather than forking them.

---

## Delivered this session (4 workstreams, 31 new passing tests)

### WS 3.1 — Face-safe crop, wired end-to-end ✅ (10 tests)

The crop engine already existed but was dead in production: `LayoutSelector`
fed it empty faces, and the renderer re-cropped centered. Fixed both ends.

- **New** `core/album/facecrop.py` — single source of truth for face-safe cover
  geometry (`face_safe_cover_crop`, `pad_face_boxes`, `face_safe_offset`).
- `core/album/layout.py` — `_cover_crop` now delegates to `facecrop` (behaviour
  preserved; verified against the existing suite).
- `core/album/template.py` — `render_spread` / `_place_slot` / `_fit` now accept
  per-photo face boxes and shift the cover crop to keep faces in-frame.
- `core/album/raster.py` — threads each placement's face boxes through the
  template render path (reordered in lockstep with `_order_by_slot_aspect`).
- `core/album/layout_select.py` — populates `PhotoItem.face_boxes` from the cache
  and stamps `face_boxes` onto every placement (so the manifest carries them).
- `core/album/orchestrator.py` — `_faces_by_path` reads cached faces and passes
  them into layout selection.
- Tests: `tests/test_ws31_face_crop.py`, `tests/test_ws31_render_select.py` —
  prove the render keeps a top-strip "face" that centered cropping dropped, and
  that no-face behaviour is unchanged (backward compatible).

### WS 3.2 — Subject-aware slot matching ✅ (9 tests)

The roadmap's core "intelligence," genuinely missing (layout was orientation-only).

- **New** `core/content_analyzer.py` — classifies each photo (portrait / group /
  large_group / detail / environmental / full_body / landscape) from face boxes +
  aspect ratio; no pixel decode.
- **New** `core/album/slot_matcher.py` — `SlotProfile` slot vocabulary,
  `compatibility_score` (composition + face-count + aspect + variety bonus), and
  `match_photos_to_slots` via Hungarian (SciPy if present) or a deterministic
  greedy fallback (no new dependency).
- Tests: `tests/test_ws32_slot_matching.py` — classification cases + that each
  photo type is assigned to its matching slot, deterministically.

### WS 4.3.1 — Multi-category event classification ✅ (7 tests)

Extended the Haldi-only colour heuristic to the full ceremony set.

- **New** `core/album/event_classifier.py` — `classify_event` →
  Haldi / Mehndi / Baraat / Reception / Ceremony / Portraits with a confidence,
  reusing `theming.dominant_color`. `event_name()` is a richer drop-in for the old
  `theming.classify_event_name`.
- Tests: `tests/test_ws43_event_classifier.py`.

### WS 3.3.1 — Face/subject cutout masks ✅ (5 tests)

- **New** `core/album/face_segmenter.py` — feathered head-and-shoulders alpha
  masks from face boxes (`segment_face_region`, `feather_mask`, `apply_mask`,
  `cutout_from_faces`), with a **graceful fallback to `None`** when a face is too
  small/absent (renderer then uses a normal shape clip — the roadmap's confidence
  fallback). Landmark convex hulls can replace the ellipse later without changing
  the interface.
- Tests: `tests/test_ws331_face_segmenter.py`.

---

## Verification

- New workstream tests: **31 passed**.
- Existing album/layout suites: **85 passed, 5 failed** — all 5 failures
  **pre-exist my changes** (2 fail on pristine `HEAD`; the 3 in
  `test_album_layout_select` come from an uncommitted budget-packing refactor
  already in the working tree — confirmed because `HEAD` + only my face-edits
  passes all 7). My changes introduced **no new regressions**.

> Note: these were run in a Linux sandbox with a subset of deps (Pillow, NumPy).
> Suites needing MediaPipe / InsightFace / psd-tools / PyQt6 weren't exercised
> here; run the full `pytest` on your Windows env to cover those.

---

## Not done — and why

- **WS 3.2 / 3.3.1 integration into the live render path.** The new analyzer,
  matcher and segmenter are built and tested as modules but are **not yet wired**
  into `orchestrator`/`raster` slot ordering (that means editing the large
  existing render files). Next step: replace `_order_by_slot_aspect` with a
  `match_photos_to_slots` call and add a `use_cutout` slot flag that invokes
  `cutout_from_faces`.
- **WS 3.4.2 — album config section.** Deliberately skipped: the config loader
  validates into typed dataclasses, so adding keys nothing consumes yet risks
  breaking config loading, and the settings would be dead until the integration
  above lands. Add these together.
- **WS 4.1 flexible layouts, 4.4 cover designer** — larger; build on 3.2.
- **WS 4.2 GPT-4V placement** — needs an OpenAI key and bills per album; out of
  scope for offline implementation (the heuristic fallback in `slot_matcher` /
  `content_analyzer` covers the no-API case).
- **WS 4.3.2 theme asset PNGs, human validation** — non-code (designer/photographer
  work), as the roadmap itself notes.

## Suggested next sprint

1. Wire `content_analyzer` + `slot_matcher` into the render path (make 3.2 live). ✅ **Done in Session 2.**
2. Add the `use_cutout` slot flag + `face_segmenter` call (make 3.3 live). ✅ **Done in Session 2.**
3. Then add the `album:` config section (3.4.2) to toggle the above. ✅ **Done in Session 2.**

---

# Phase 3 & 4 — Implementation Log (Session 2)

Completes the "not done" items from Session 1 by wiring all three new modules
into the live render pipeline. **22 new integration tests, all passing.**

## Delivered this session

### WS 3.2 — Subject-aware slot matching, wired end-to-end ✅

`render_spread_template` now uses composition-aware photo→slot assignment so a
portrait photo (large face) lands in the tall slot and a landscape shot lands in
the wide slot, instead of the old pure-aspect-ratio sort.

- **New** `core/album/raster.py` `_order_by_content()` — calls
  `content_analyzer.analyze` per photo (face boxes + aspect → composition type)
  then `slot_matcher.match_photos_to_slots` (Hungarian/greedy) to build the
  optimal photo→slot permutation. Falls back to `_order_by_slot_aspect` on any
  import or solver failure (no new hard dependency).
- `render_spread_template` now calls `_order_by_content` (when
  `smart_slot_ordering=True`) instead of the old `_order_by_slot_aspect`.
- Tests: `tests/test_ws32_wired.py` — portrait→tall slot, landscape→wide slot,
  fallback on import error, determinism (8 tests).

### WS 3.3.1 — Face cutout masks, wired end-to-end ✅

`_place_slot` in `template.py` now conditionally applies a feathered
head-and-shoulders alpha cutout (the roadmap's editorial silhouette look) when
the slot is authored as `use_cutout=True` and the album config opts in.

- `core/album/template.py` `TemplateSlot` gains `use_cutout: bool = False`
  (fully backward-compatible; all existing JSON templates default to `False`).
- `_place_slot` calls `face_segmenter.cutout_from_faces` when
  `use_cutout and slot.use_cutout and face_boxes`; falls back to the existing
  `_shape_mask` hard clip when the segmenter returns `None` (face too small) or
  when `use_cutout=False`.
- Hero slot (index 0) of `classic-3`, `classic-4`, `classic-5` marked
  `use_cutout=True` — these are the large portrait-scale slots where a
  feathered silhouette is the intended editorial look.
- Tests: `tests/test_ws331_wired.py` — cutout produces fewer opaque pixels than
  RECT clip, tiny-face fallback, no-face fallback, field roundtrip through
  `to_dict/from_dict`, hero slot verification (7 tests).

### WS 3.4.2 — Album config section ✅

Two album-level feature flags, stored in the `album_spec` meta dict (same
mechanism as `theme`, `cover_title`, `cover_date` — zero YAML validator risk):

- `smart_slot_ordering` (bool, default `True`) — enables WS 3.2 matching.
  Set to `False` to revert to the legacy aspect-sort.
- `use_cutouts` (bool, default `False`) — opt-in: enables WS 3.3.1 cutouts on
  eligible slots.

Implementation:
- `core/album/raster.py` `_album_flags(project)` — reads both from `album_spec`
  meta dict; graceful default fallback on any attribute error.
- `render_spread_template` reads flags via `_album_flags` before ordering/cutout.
- `core/album/orchestrator.py` `AlbumOrchestrator.__init__` accepts
  `smart_slot_ordering=True` / `use_cutouts=False` kwargs; writes them into
  `album_meta` in `_prepare` so they persist in the manifest.
- Tests: `tests/test_ws342_album_flags.py` — flag reads, defaults, graceful
  bad-meta handling, and orchestrator round-trip (7 tests).

## Verification

- New integration tests: **22 passed**.
- Full non-Qt suite: **491 passed, 6 failed** — all 6 failures are pre-existing
  (verified: same failures on `HEAD` before this session's changes).
  **Zero new regressions.**

> Note: run `python -m ui_qt.main` on Windows to confirm the import chain is
> intact, and optionally `generate()` on a real folder to see portrait photos
> assigned to tall slots in the output spreads.

