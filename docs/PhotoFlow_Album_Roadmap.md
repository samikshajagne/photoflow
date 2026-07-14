# PhotoFlow Album Generation — Architecture & Roadmap

**Status:** Architecture/roadmap (no implementation). For review.
**Date:** 2026-06-19
**Target:** "Select wedding folder → Generate complete wedding album."

---

## 1. Where we are

Built and tested today (engine logic, mostly standalone):

| Capability | Module(s) | State |
|---|---|---|
| Scan / import | `core/scanner.py` | done |
| Duplicate / blur / usability / quality / BestShots + tiers | `core/duplicate_detector`, `blur_detector`, `quality_scorer`, `pipeline`, `organizer` | done, wired into the analysis pipeline |
| Face **detection** (+ regions) | `core/face_detector.py` | done |
| Manual category overrides / session persistence | UI + persistence | done |
| Face **embedding** (crop→vector) | `core/face_embedder.py` | **seam only — no model wired** |
| Person clustering | `core/person_cluster.py` | done (logic) |
| Identity orchestration + queries | `core/identity.py` | done (logic) |
| Label-once persistence | `persistence/identity_store.py` | done (logic) |
| Capture time + event segmentation | `core/timeline.py` | done (segmentation only) |
| Auto-edit recipes | `core/auto_edit.py` | done (engine) |
| Section primitives | `core/album/sections.py` | done (primitives) |
| Layout placement | `core/album/layout.py` | done (geometry) |
| Export manifest + retouch round-trip | `core/album/export.py` | done (JSON + interface) |

**Key observation:** we have most of the *engines* but almost none of the
*connective tissue*. The remaining work is integration, a few genuinely missing
modules, and the model/UI hookups — not re-inventing analysis.

---

## 2. Missing modules

| # | Missing piece | Why it's missing today | Type |
|---|---|---|---|
| M1 | **Embedding model backend** (ArcFace/InsightFace, CPU/ONNX) | embedder is a seam with no model | hard dependency for identity |
| M2 | **Event Classification** (name segments: pre-events / ceremony / reception) | timeline only *segments* by gaps; nothing *names* chapters | new module |
| M3 | **Story Builder** (assemble the full ordered wedding narrative) | only section *primitives* exist | new orchestration module |
| M4 | **Album Orchestrator** + canonical **AlbumProject state** + persistence + caching | nothing runs the full chain; no single state object | the spine |
| M5 | **Auto-Edit Pipeline stage** (run recipes across album set, store sidecars, render proxies) | engine exists; batch stage + storage don't | integration stage |
| M6 | **Layout Selection policy** (section → template style, spreads-per-section, hero treatment) | engine places a given list; nothing *chooses* templates per section | thin policy layer |
| M7 | **Concrete Export renderer** (manifest → export-ready PDF/JPEG spreads) and/or editable-tool adapter | only the JSON manifest exists | leaf adapter |
| M8 | **Embedding/analysis cache** (persist per-file results keyed by content hash) | re-running re-analyzes everything | performance dependency |
| M9 | **UI surfaces** (cluster-labeling screen, album preview, size config, export button) | desktop UI has browse/analyze only | GUI work |

---

## 3. Dependency graph

```
                          ┌──────────────┐
                          │  Scan/Import │
                          └──────┬───────┘
          ┌──────────────────────┼───────────────────────────┐
          ▼                      ▼                            ▼
  Dup / Blur / Quality    Face Detection (regions)     EXIF capture time
  → BestShots + tiers            │                     (timeline)
   (DONE, in pipeline)           ▼                            ▼
          │               Face Embedding  [M1 model]   Event Segmentation (DONE)
          │                      │                            ▼
          │               Person Clustering (DONE)     Event Classification [M2]
          │                      ▼                            │
          │               Label-once + Persist (DONE)         │
          │                      ▼                            │
          │                 persons_present              named events
          │                      └──────────────┬─────────────┘
          └───────────────┬─────────────────────┘
                          ▼
                    Story Builder [M3]   ◄── BestShots + tiers
                    (ordered AlbumProject: sections + photos)
                          ▼
                Auto-Edit Pipeline stage [M5]  (EditRecipe per photo)
                          ▼
                  Layout Selection [M6]  (sections → spreads, templates)
                  + Layout placement (DONE)
                          ▼
                  Export System  (manifest DONE → renderer/adapter [M7])

   ┌──────────────────────────────────────────────────────────────┐
   │ ALBUM ORCHESTRATOR [M4] wraps this whole vertical, owns the    │
   │ persisted AlbumProject state + analysis/embedding cache [M8],  │
   │ and re-runs idempotently (manual overrides preserved).         │
   │ UI surfaces [M9] read/write the same AlbumProject.             │
   └──────────────────────────────────────────────────────────────┘
```

This is the user's linear example, made into a DAG with two parallel feed
branches (quality, and the two identity/time branches) converging at the Story
Builder.

---

## 4. Independent vs. coupled work

**Buildable independently** (stable contracts, no blocking on each other):
- M2 Event Classification — needs only timeline + lightweight scene features.
- M5 Auto-Edit Pipeline stage — needs a photo list + face regions.
- M6 Layout Selection — needs sections + `AlbumSpec` (can develop on stub sections).
- M7 Export renderer — needs `Spread`s + images.
- M1 Embedding model — slots behind the *already-fixed* `embed_backend` seam.

**Strictly sequential / coupled:**
- Identity chain: embedding → clustering → labeling → `persons_present`.
- Narrative chain: `persons_present` + named events + quality → **Story Builder**
  → Layout → Export.
- Person-specific sheets (bride/groom/couple/family) **cannot** exist before
  identity works; the album must **degrade gracefully** to a time/quality-only
  album when identity is absent.

**The integration spine (M4) gates everything** — not because of a missing
algorithm, but because there is no canonical state object the stages share.

---

## 5. Final architecture (the seven subsystems)

**A. Person Identification.** `face_detector` → `face_embedder` (M1 model) →
`person_cluster` → `identity.PersonIndex`; labels persisted by centroid
(`identity_store`). Output contract: `persons_present: {photo → {labels}}` plus
solo/couple/group queries. *Contract already exists and is consumed by Sections
— so the model can be swapped in without downstream rewrites.* Must be optional:
no model / unlabeled clusters → no person sheets, album still builds.

**B. Wedding Event Classification.** Input: timeline + light cues. Pipeline:
`segment_events` (gaps) → classify each segment into a chapter
(pre-events / ceremony / reception …) using **relative order + duration +
optional scene cues (indoor/outdoor, brightness, flash)**, never absolute clock
assumptions. Output: named, ordered `EventSegment`s, user-renamable. Build M2 on
top of existing segmentation.

**C. Story Builder (M3).** Input: `persons_present`, named events,
BestShots + tiers, and `SectionSpec` rules. Output: an ordered `AlbumProject`
(cover → couple → bride → groom → families → ceremonies(chronological) →
reception). Encodes the *narrative template* and degrades gracefully (drops
person sheets if identity missing; merges/splits chapters from events). Sits
directly on the existing `sections.py` primitives.

**D. Album Orchestrator (M4).** The spine. Owns the canonical, **persisted
`AlbumProject` document** (analysis results + identity + events + sections +
spec + edits + spreads + manual overrides) and the **analysis/embedding cache
(M8)**. Runs scan → analysis(pipeline) → identity → events → story → auto-edit →
layout → export, **idempotently** (re-runs never clobber human edits). Single
entry point behind "Generate album". This is what the UI and any CLI call.

**E. Auto Editing Pipeline (M5).** `AutoEditor` over the album's photos →
`EditRecipe` per photo stored as **non-destructive sidecars** in the project;
optional rendered proxies/finals for preview/export. Flags portraits
`retouch_needed`. Originals never modified.

**F. Layout Selection (M6).** Per section: choose template style (e.g. bride →
full-bleed portrait; family → grid; ceremony → mixed grids), spreads-per-section,
and hero treatment; hand each spread's photo set to the existing **face-safe**
`AlbumLayoutEngine` honoring `AlbumSpec` (size/bleed/DPI/gutter). Output: spreads.

**G. Export System.** `album_project.json` manifest is the **source of truth**
(done). Leaves: **(g1) a concrete renderer** → export-ready PDF/JPEG spreads
(self-contained, no external tool — recommended MVP); **(g2) editable-handoff
adapter** (IDML/Affinity/PSD/album-tool) later. Retouch round-trip already
modeled (`needed`→`done`, `relink`).

---

## 6. Implementation plan — fewest phases

Ordered to **set contracts first** so later modules slot in without rewrites,
and to be **runnable end-to-end as early as possible**.

**Phase 1 — The Spine + a real album (no identity yet).**
M4 Album Orchestrator + canonical `AlbumProject` state + persistence + cache
(M8); M5 Auto-Edit stage; M6 Layout Selection; M7(g1) render exporter; a basic
M3 Story Builder using **time + quality only** (cover, ceremonies chronological,
reception). Deliverable: *select folder → export-ready album* (no person sheets).
This proves the whole pipeline and freezes every inter-module contract.

**Phase 2 — Identity, for real.**
M1 embedding model (CPU/ONNX) behind the existing seam + clustering tune +
label-once UI + persistence. Deliverable: clusters you can label once. Slots
into the Phase-1 spine; no rewrites because `persons_present` is already the
contract.

**Phase 3 — Full narrative.**
Upgrade M3 Story Builder to add person sheets (bride/groom/couple/families) and
M2 Event Classification (named chapters). Deliverable: the *complete* wedding
album content.

**Phase 4 — UI & handoff polish.**
M9 album preview + size config + export button + override editing; M7(g2)
editable-tool adapter if wanted. Deliverable: production UX.

Four phases; a usable album ships at the end of **Phase 1**, the full
person-grouped album at **Phase 3**.

---

## 7. Risks, dead ends, and mistakes to avoid

1. **One canonical state object — or rewrites.** Every stage must read/write the
   single persisted `AlbumProject`. Passing ad-hoc dicts between stages is the
   #1 future rewrite. *Build the state model before the stages.*
2. **Never hard-require identity.** Person sheets must be optional and the album
   must degrade to time/quality-only. Hard-coupling identity into album
   generation is a dead end (unlabeled clusters, missing model, declined faces).
3. **Model must run on the user's machine.** Choose a **CPU-friendly ONNX** face
   model; a GPU-only dependency is a dead end on a typical Windows laptop. Keep
   `distance_max` configurable; recognition quality varies — don't hardcode it.
4. **Non-destructive editing, always.** Keep edits as recipes/sidecars; never
   flatten originals. Rendered spreads are *derived* artifacts so retouch
   round-trip and re-layout stay possible.
5. **Don't classify events by wall-clock.** Camera clocks drift; destination/
   multi-day/multi-culture weddings (haldi/mehndi/sangeet/ceremony/reception)
   break "evening = reception" rules. Use relative gaps/order + scene cues +
   user renaming. Avoid hardcoding one wedding's structure.
6. **Recognition is a suggestion, not truth.** Mislabeling bride/groom is
   high-cost; require a one-time human confirmation step, allow correction, keep
   it local-only, and never auto-finalize person sheets without confirm.
7. **Persist analysis & embeddings (M8).** Re-embedding/re-analyzing a
   900–3000-photo shoot every run is unusable. Cache by content hash; make
   re-runs incremental and idempotent with overrides preserved.
8. **Mixed orientations & aspect ratios.** Templates and cover-fit must handle
   portrait *and* landscape; don't assume landscape. (Placement is face-safe —
   keep it that way through Layout Selection.)
9. **Input scope.** Decide early: sRGB JPEG first; RAW/color-profiles later.
   Silently mishandling RAW/wide-gamut is a quality dead end.
10. **Keep export tool-agnostic.** The JSON manifest stays the source of truth;
    renderers/adapters are leaves. Don't bury album logic inside an
    InDesign-specific format.

---

## 8. The single highest-value next task

**Build the Album Orchestrator + the canonical `AlbumProject` state model
(M4), and run the existing analysis end-to-end into a render-exported,
time/quality-only album (Phase 1).**

Why this before all others:

- **It freezes the contracts.** Every other module (identity, events, story,
  auto-edit, layout, export) reads/writes the `AlbumProject`. Defining it first
  turns each later module into a slot-fill instead of a rewrite — directly
  serving the "minimize future rewrites" goal.
- **It makes the system runnable now.** Today nothing connects folder → album.
  The spine yields a working, demoable, export-ready album immediately (even
  without person sheets), which is the fastest path to validating the whole
  pipeline and surfacing integration problems early.
- **It unblocks parallelism.** Once the state object and stage interfaces exist,
  identity (M1), event classification (M2), and the export renderer (M7) can all
  proceed independently against fixed contracts.
- **Identity is the close second, not the first.** It's essential for person
  sheets, but its *output contract already exists and is already consumed by the
  section builder*, so it can be tuned and dropped into a spine that already
  knows where its output goes. Building identity first would mean designing its
  integration against a consumer that doesn't exist yet — inviting the very
  rewrites we're trying to avoid.

Concretely, the first commit is the `AlbumProject` schema + the orchestrator
skeleton that runs the existing pipeline and emits a manifest — everything else
hangs off that spine.
