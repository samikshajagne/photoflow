# PhotoFlow — Phase 3 & 4 Roadmap vs. Actual Codebase (Gap Analysis)

**Purpose:** Map every workstream in `PHASE_3_4_DETAILED_ROADMAP.md` to what already
exists in `core/album/`, so we build the missing 20% instead of duplicating the
existing 80%.
**Audited:** 2026-07-18 against the current `core/`, `ui_qt/`, and `data/` tree.
**Bottom line:** The roadmap was written as if `core/album/` were an empty grid MVP.
It isn't. Much of Phase 3 is already implemented under different filenames. Taking
the roadmap literally (new `crop_engine.py`, `slot_schema.py`, `frame_renderer.py`,
…) would fork working code.

---

## Legend

| Tag | Meaning |
|-----|---------|
| ✅ **DONE** | Functionality exists and works; roadmap's "new file" would duplicate it. |
| 🟡 **PARTIAL** | Core exists but is incomplete, or exists but is **not wired into the live path**. |
| ❌ **MISSING** | Genuinely not implemented. Real build target. |

---

## The single most important finding

**The face-safe crop engine already exists and is quite sophisticated — but it is
dead code in production.** Two independent breaks:

1. `LayoutSelector.select()` constructs every `PhotoItem` with `face_boxes=()`
   (`core/album/layout_select.py:151`). So the face-aware crop in
   `AlbumLayoutEngine._cover_crop` never receives any faces.
2. The actual renderer (`render_spread_template`, `core/album/raster.py:457`)
   **ignores the crop stored on each placement** and re-crops centered via the
   template engine (`_order_by_slot_aspect` + `_fit`, `template.py`). Even if faces
   were wired into step 1, the rendered PNG/PDF/PSD would still discard the result.

The roadmap's #1 success criterion — *"zero faces cut"* — is therefore currently
**not met**, not because the algorithm is missing, but because it is disconnected at
both ends. This is the highest-value, lowest-effort fix in the entire document.

---

## Phase 3

### Workstream 3.1 — Face-Aware Intelligent Cropping

| Item | Status | Evidence / Notes |
|------|--------|------------------|
| 3.1.1 Crop engine (`crop_engine.py`) | 🟡 PARTIAL | Already implemented as `AlbumLayoutEngine._cover_crop` / `_pad_face_boxes` / `_face_safe_offset` / `_pulls_left` (`layout.py:734-885`). Does head-and-shoulders padding, keeps face spans inside the crop, and pulls faces away from the gutter. Missing vs. roadmap: no standalone module, no numeric **viability score**, no "fit vs fill" mode, no per-slot crop caching. |
| 3.1.2 Crop into pipeline + cache | ❌ MISSING | `AnalysisCache` stores `faces` but no `crop_suggestions`. Crop is computed at layout time, not analysis time. |
| 3.1.3 Crop application in album gen | 🟡 PARTIAL | Crop **is** written into `SpreadRecord.placements[].crop`, but the render path (`raster.py`) throws it away (see finding above). |

**Verdict:** The crop *algorithm* is ~done. The crop *wiring* is broken. Don't build
`crop_engine.py` from scratch — connect and harden what exists.

---

### Workstream 3.2 — Subject-Aware Slot Matching

| Item | Status | Evidence / Notes |
|------|--------|------------------|
| 3.2.1 Content analyzer (portrait/group/detail) | ❌ MISSING | No composition classification anywhere. The system only ever looks at **aspect ratio** (`_assign_by_orientation` in `layout.py:656`, `_order_by_slot_aspect` in `raster.py`). Face count / face coverage / subject isolation are never computed for layout. |
| 3.2.2 Slot schema (`slot_schema.py`) | 🟡 PARTIAL | `TemplateSlot` / `SpreadTemplate` (`template.py:75-186`) define geometry, shape, border, fit — richer than the roadmap's "current" example. But slots have **no semantic type**: no `ideal_composition`, no `ideal_face_count`. They're shapes, not subject-typed slots. |
| 3.2.3 Photo-to-slot matcher (Hungarian) | ❌ MISSING | Matching today is orientation-only greedy zip. No compatibility score, no variety bonus, no bipartite optimization. **`scipy` is not a dependency** — Hungarian needs it added, or a greedy fallback. |
| 3.2.4 Matcher integration | ❌ MISSING | Fill order is sequential/orientation-based. |

**Verdict:** This is the roadmap's actual "intelligence," and it is genuinely absent.
The real build. Reuse `TemplateSlot` as the slot side rather than inventing `slot_schema.py`.

---

### Workstream 3.3 — Face Cutout Masks & Artistic Frames

| Item | Status | Evidence / Notes |
|------|--------|------------------|
| 3.3.1 Face segmentation (`face_segmenter.py`) | ❌ MISSING | No landmark-based person cutout. InsightFace is used for **embeddings only** (`insightface_backend.py`), not for segmentation masks. `brushmask.py` is a *procedural* torn-edge, not a subject cutout. |
| 3.3.2 Artistic frame engine (circle/oval/diamond/rounded/feathered) | ✅ DONE | `template.py` `SHAPES = {rect, rounded, circle, oval, diamond, brush}` + `_draw_border` + shadow (`_paste_shadow`). Plus a bonus procedural painterly edge with feathering (`brushmask.py`). Building `frame_renderer.py` would duplicate this outright. |
| 3.3.3 Cutout integration | 🟡 PARTIAL | Shapes composite into spreads today, but a "cutout" = a shape clipping a *rectangular crop*, **not** a background-removed subject. Background color sampling already exists (`theming.py`, `Background(BG_SAMPLED)`). |

**Verdict:** Frames = done. True face/subject **segmentation** is the only missing piece here.

---

### Workstream 3.4 — Template & Config Updates

| Item | Status | Evidence / Notes |
|------|--------|------------------|
| 3.4.1 Template schema extension | 🟡 PARTIAL | `SpreadTemplate` already serializes to/from JSON (`to_dict`/`from_dict`/`to_json`). Missing: per-slot `use_cutout` / `frame_style` semantics and in-template `text_overlays` (text is handled separately by `textlayer.py` + `sections.py`). |
| 3.4.2 Config defaults (`album.layout.*`) | ❌ MISSING | `data/default_config.yaml` has **no album section** at all (only `io`, `logging`, `thresholds`, `scoring_weights`, `performance`). Album settings flow through `AlbumSpec` + `album_settings_dialog.py`, not config keys. |

---

### Workstream 3.5 — UI for Manual Override

| Item | Status | Evidence / Notes |
|------|--------|------------------|
| Album preview panel | 🟡 PARTIAL | `preview_view.py` renders spreads top-to-bottom (read-only). `album_settings_dialog.py` collects page size, density, and cover name/date. **Missing:** per-slot compatibility display, drag-drop swap between slots, per-slot cutout toggle. |

---

### Workstream 3.6 — Testing & Validation

| Item | Status | Evidence / Notes |
|------|--------|------------------|
| Unit tests | 🟡 PARTIAL | Strong existing coverage: `test_album_layout`, `test_album_layout_select`, `test_album_template`, `test_album_theming`, `test_orientation_match`, `test_template_variety`, `test_layout_budget`, etc. **Missing** tests only for modules that don't exist yet (crop viability, content analyzer, slot matcher, segmenter). |
| Integration / human validation | ❌ / N/A | Requires the missing features + real photographers. |

---

## Phase 4

| Workstream | Status | Evidence / Notes |
|-----------|--------|------------------|
| 4.1 Variable aspect / flexible slots | 🟡 PARTIAL | Count-based templates with variety rotation already exist (`choose_template`, `template_for`, `default_templates`, `select_template`; `test_template_variety`). Variable per-spread counts exist via density/page-budget (`layout_select.py`). **Missing:** composition-driven `slot_pool` selection, `layout_rules.py`, `spread_layout_calculator.py` `LAYOUT_PATTERNS` (positions come from `Frame` templates instead). |
| 4.2 GPT-4V smart placement | ❌ MISSING | No `placement_advisor.py`, no `placement_heuristics.py`, no OpenAI dependency. |
| 4.3.1 Event classification + color sampling | 🟡 PARTIAL | `dominant_color()` sampling = done (`theming.py`). `classify_event_name()` maps color → **"Haldi" only**, else `None` (`theming.py:111`). `timeline.segment_events` does chronological segmentation. **Missing:** multi-category (mehndi/baraat/ceremony/reception) classification. |
| 4.3.2 Theme asset library (`data/themes/…`) | ❌ MISSING | No `data/themes/` directory. Theming is procedural color only — no background/decoration PNG assets. (Roadmap itself flags this needs a professional designer.) |
| 4.3.3 Asset rendering into spreads | ❌ MISSING | Procedural backgrounds only. Section titles like "Haldi" render via `textlayer.py`, but no decorative asset layering. |
| 4.4 Cover designer (`cover_designer.py`) | 🟡 PARTIAL | Cover is a 1-photo "cover" section; couple names + date are captured in `album_settings_dialog.py` and rendered via `textlayer`/`sections`. **Missing:** dedicated decorative header + hero cutout + tagline library. |
| 4.5 Template marketplace | ❌ MISSING | Explicitly deferred in the roadmap itself. |
| 4.6 Phase 4 tests | ❌ MISSING | Depend on the above. |

---

## What's actually missing (the real backlog)

Ranked by value-to-effort, ignoring items that duplicate existing code:

1. **Wire faces into cropping + honor the crop at render time** (completes 3.1).
   *Highest value, lowest effort.* Makes the existing face-safe crop actually run.
   Directly satisfies the "zero faces cut" success criterion. Prerequisite for
   everything else — intelligent matching is pointless if the renderer re-crops centered.
2. **Content analyzer + subject-aware slot matching** (3.2.1 + 3.2.3). The roadmap's
   core intelligence. Build on `TemplateSlot`; add a greedy matcher (or add `scipy`
   for Hungarian).
3. **Face/subject segmentation for true cutouts** (3.3.1). The only missing piece of
   the otherwise-complete frame system.
4. **Multi-category event classification** (4.3.1). Extend the existing Haldi-only
   `classify_event_name` to the full ceremony set.
5. **Album config section** (3.4.2) + **slot-level UI override** (3.5). Plumbing/UX.
6. Theme asset library (4.3.2), GPT-4V placement (4.2), cover designer (4.4) — larger,
   depend on external assets/APIs/design work.

---

## Recommended next build target

**Start with #1: connect the face-safe crop end-to-end.** Concretely:

- Populate `PhotoItem.face_boxes` in `LayoutSelector.select()` from the analysis
  cache's `faces` entries (they're already computed and cached during the pipeline).
- Make `render_spread_template` (`raster.py`) honor the placement's stored `crop`
  instead of re-cropping centered — or feed face boxes into the template `_fit` so
  its crop is face-aware too.
- Add unit tests: face box fully inside the rendered crop; multi-face span preserved;
  regression test proving a known face isn't clipped.

This is a focused, testable change (~1 file of logic + wiring + tests), it makes a
feature you already paid for actually work, and it unblocks #2 (matching). Once faces
are honored at render time, subject-aware slot matching (#2) becomes the next sprint.

I'd suggest **not** building `crop_engine.py`, `slot_schema.py`, `frame_renderer.py`,
or `frame`/shape modules as new files — they duplicate `layout.py`, `template.py`, and
`brushmask.py`. Build `content_analyzer.py` and `slot_matcher.py` as genuinely new
modules when we reach #2.
