# PhotoFlow — Development Roadmap

*Goal: turn a raw wedding-shoot folder into a designed, deliverable album fast — with the app doing the heavy lifting (detection, culling, layout) and the photographer doing only the judgment work (labeling people, picking a theme, minor tweaks).*

---

## 1. Target output (what "done" looks like)

Reference: the manually-designed sample album (46 double-spreads + 2 covers, 5400×3600).

Each spread is a **composited layout**, not a placed photo. The design system observed:

- **Photo cutouts / masks** — brush/watercolor edges, circles, ovals, rotated diamonds, bordered rectangles (not just full rectangles).
- **Sampled backgrounds** — solid fills pulled from each event's dominant color (Haldi = yellow, etc.) plus watercolor florals and flourish dividers.
- **Text + graphics system** — section titles, romantic quotes, script + sans fonts, the couple's names in decorative Devanagari, repeating motifs.
- **One black-&-white conversion per spread**, deliberately, for contrast.
- **Event sequencing with color themes** — Haldi → Mehndi → Baraat → Varmala → Reception → Portraits.

The realistic path to this is a **themed template system** (like the PSD template pack the sample photographer used), not generative design. The app fills pre-designed templates; it does not invent layouts from scratch.

---

## 2. Where the existing segregation fits

The current BestShots / Duplicates / Blurry / Review classification is **kept and reused** — its role changes from *final deliverable* to *engine underneath the album*:

| Category | New role in the album flow |
|---|---|
| **Duplicates** | Culled before labeling; hidden but reviewable/rescuable |
| **Blurry** | Culled before labeling; hidden but reviewable |
| **Best Shots** | Priority pool the album pulls from, per person and per event |
| **Review** | Borderline set; used to fill spreads when best shots run short |

Surfaced as an **optional review panel** — hidden by default (no mandatory step), openable for photographers who want to inspect or override what got dropped. This preserves the existing sticky-override system in `core/album/orchestrator.py`.

---

## 3. New user flow (people-first, analyze-once)

```
1. Open folder
      └─ ONE background analysis pass, cached once:
         scan → dedup → blur → face detection → embeddings → quality
      (optional: "Review cull" panel to inspect/override dropped photos)

2. Label people
      └─ app shows ONE thumbnail per person cluster
      └─ user names only the key people (Bride, Groom, family); guests stay "unknown"

3. Pick a template / theme
      └─ gallery of themed collections with live preview

4. Generate album
      └─ auto-place photos into template slots, cutouts, backgrounds, text, B&W
      └─ export (PNG / JPG / PDF / PSD + Photoshop JSX)
```

**Core principle: analyze once, cache everything, reuse forever.** Face detection happens exactly once and feeds culling, clustering, and album generation. Changing a label or switching templates in steps 2–4 never re-runs detection.

---

## 4. Workstream A — Flow refactor (speed + fewer clicks)

*These are confirmed inefficiencies in the current code. Highest ROI, do first.*

**A1. Make the pipeline write to `AnalysisCache`.** *(critical)*
`core/pipeline.py` never populates the cache, so "Analyze Folder" and "Generate Album" each run the full detection pipeline from scratch — the pipeline runs **twice**. Write detections, embeddings, and quality to `AnalysisCache` on the first pass; the orchestrator's `_analyze` already reads it.

**A2. Reuse face detections in the identity stage.** *(high)*
`orchestrator._run_identity` calls `detector.detect()` on every candidate again, though the pipeline already detected faces for quality scoring (`pipeline.py:413`). Cache and reuse — cuts face detection from 3 passes to 1.

**A3. Collapse Open + Analyze into one action.** *(medium)*
Currently two clicks that each scan the same folder. Merge into "Open & Analyze" — scan streams straight into analysis.

**A4. Make "Label People" lightweight.** *(high)*
Today it calls `_generate_album()`, re-running events, story, layout, spreads, and auto-edit. It should only re-cluster / rename and refresh affected sections, reusing cached embeddings.

**A5. Remove the no-op "Review" step and pick one control system.** *(medium)*
The wizard "Review" step just flips a flag. Drop it (fold into the optional cull panel). The toolbar and wizard bar duplicate all six actions — keep the guided wizard as primary, demote the toolbar to power-user shortcuts.

**Expected effect:** ~6 clicks + 2 full pipeline runs → ~3 clicks + 1 pipeline run. Roughly 2–3× wall-clock speedup on large shoots before any algorithmic tuning.

---

## 5. Workstream B — Design / template engine (to reach the sample look)

*Sequenced by visual impact per unit of effort.*

**B1. Template library + schema.** *(foundational)*
Define templates as slot maps (position, size, shape, rotation, border) + decorative layers (background, frames, flourishes, text placeholders), grouped into themes/collections. Extend `layout_select.py` and `photoshop_jsx.py` to consume real templates instead of bare grids.

**B2. Cutout engine.** *(highest visual impact)*
Subject masking (reuse InsightFace/MediaPipe) → feathered / brush / circle / diamond frames. Single biggest differentiator from current output.

**B3. Event naming + per-event color theming.** *(high)*
Replace "Event 1/2/3" with classified events (Haldi/Mehndi/Baraat/Ceremony/Reception) and matching color themes sampled from the photos.

**B4. Text overlay system.** *(high)*
Themed caption/quote library with Devanagari support; a cover designer for the couple's names and date.

**B5. Auto black-&-white pick.** *(medium, low effort)*
Select one photo per spread for B&W conversion.

**B6. Sampled backgrounds + decorative asset library.** *(medium)*
Dominant-color fills + watercolor/floral/flourish asset sets per theme.

---

## 6. Suggested sequencing

1. **Phase 1 — Flow & speed:** A1, A2, A4 (kill the duplicate work), then A3, A5 (streamline clicks). Ships a faster app on the current output.
2. **Phase 2 — People-first flow:** wire the new step order (§3) on top of the cached pipeline; cluster-based labeling UI.
3. **Phase 3 — Template engine:** B1 → B2 → B3, which gets output visibly close to the sample.
4. **Phase 4 — Polish:** B4, B5, B6 and the cover designer.

---

*This roadmap keeps every existing engine (scanner, dedup, blur, quality, faces, clustering, auto-edit, layout, export). It re-sequences them behind a people-first flow, removes duplicated work, and adds a template layer on top to reach the designed-album look.*
