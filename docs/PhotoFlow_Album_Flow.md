# PhotoFlow Album Generation — Workflow Design

**Status:** Flow/architecture proposal (no implementation). For review.
**Date:** 2026-06-19

## Goal

Produce a wedding album of edited sheets at customizable sizes: a cover with
couple shots, then bride-solo, groom-solo, family and close-family sheets, and
each ceremony in chronological order — with photos auto-corrected in PhotoFlow
and beauty retouching handed off to an external editor, exported as an editable
layout project.

## Decisions locked (from review)

1. **Editing:** PhotoFlow does automatic corrections (white balance, exposure,
   contrast, straighten, face-aware crop). **Beauty retouch is handed off** to
   an external editor — not automated in-app.
2. **Person grouping:** add **face clustering**; the photographer **labels each
   cluster once** (bride, groom, family A/B, …). No per-photo manual tagging.
3. **Output:** an **editable layout handoff** (a layout project, not a flat
   PDF), so the design can be fine-tuned downstream.
4. **Structure:** a **custom section builder** — the photographer defines
   sections and order per album.

---

## The flow at a glance

```
        ┌─────────────────────────────────────────────────────────────┐
        │  EXISTING: scan → dedupe → usability → faces(detect) →        │
        │            quality → BestShots + tiers                        │
        └─────────────────────────────────────────────────────────────┘
                                   │
   A. IDENTITY        face embeddings → cluster people → label clusters once
                      (+ read EXIF time, segment ceremonies)
                                   │
   B. AUTO-EDIT       per album candidate: WB / exposure / contrast /
                      straighten / face-aware crop  → non-destructive edits
                                   │
   C. SECTION BUILDER define sections & order; auto-populate each from
                      identity + tiers + time; photographer adjusts
                                   │
   D. LAYOUT          album spec (size/bleed/DPI) + spread templates;
                      face-safe placement across the gutter
                                   │
   E. EXPORT          editable layout project + linked high-res assets
                      → external retouch round-trip → final
```

Each stage writes to a persisted **album project** so the photographer can stop
and resume, and so re-running analysis never destroys manual choices (the same
principle as the manual-override design).

---

## Stage A — Identity (the new capability)

Person sheets require knowing *who is who*, which is face **recognition**, not
the detection PhotoFlow does today. Minimal, contained addition:

1. **Embeddings.** For each detected face region (we already have the boxes),
   compute a face embedding vector (e.g. an ArcFace/InsightFace-style model).
   This is per-face, not per-photo.
2. **Cluster.** Group embeddings into person clusters (e.g. HDBSCAN/agglomerative
   on cosine distance). Each cluster ≈ one person across the shoot.
3. **Label once.** The photographer reviews clusters in a small UI and tags the
   important ones: *Bride*, *Groom*, *Family (bride side)*, *Family (groom
   side)*, *Close family*… Unlabeled clusters (guests) are fine to leave.
4. **Persist.** Labels are stored keyed by cluster, and each photo gains a
   `persons_present` set. This survives re-runs.

Also in this stage: read **EXIF timestamp** per photo (for chronological order)
and **segment ceremonies** — by large time gaps and/or by the subfolder a photo
came from (haldi/mehndi/ceremony/reception). The photographer can rename/merge
segments.

**Privacy note:** recognition is local-only and used solely to group this
shoot; it's worth stating that explicitly in the UI given it's a shift from the
detection-only stance.

---

## Stage B — Auto-edit (corrections only)

For every album candidate, apply tasteful automatic corrections:

- White balance / color cast correction
- Exposure + contrast normalization (respecting the high-key wedding look — no
  crushing the dress to grey)
- Straighten (horizon/vertical) and **face-aware crop** suggestions (rule of
  thirds, keep faces out of the cut and away from the gutter)

**Non-destructive:** originals are never overwritten. Store either edited
high-res renders in an `Album/Edited/` working set, or — better for the handoff
— adjustment parameters as **sidecars** so the external tool can honor them.

**Beauty retouch is not done here.** Instead, flag photos likely to need it
(portraits / close-ups of labeled people) with a `retouch_needed` hint and a
`retouch_status` the round-trip in Stage E tracks.

---

## Stage C — Section builder (custom)

The photographer composes the album from ordered **sections**. Each section is
populated by a rule over the data we now have, then hand-adjustable:

| Section (example) | Auto-populate rule |
|---|---|
| Cover | top-tier (Hero) couple shot |
| Couple | photos containing Bride **and** Groom |
| Bride solo | contains Bride, no other *labeled* person |
| Groom solo | contains Groom, no other *labeled* person |
| Families | contains any Family-labeled cluster |
| Close family | contains Close-family cluster |
| Ceremony N | time-segment N, chronological |

Rules draw on **BestShots + tiers** (quality), **identity** (who's present), and
**time** (order/segment). The photographer then drags photos in/out and
reorders — those edits are sticky overrides, exactly like the category
overrides. Sections, their order, and their contents are all custom per album.

---

## Stage D — Layout & customizable sizes

**Album spec** (configurable): page/spread size (e.g. 12×12, 10×10, 8×12 in),
orientation, DPI (e.g. 300), bleed, safe margins, gutter width, single-page vs
double-page spreads.

**Spread templates:** 1-up full-bleed hero, 2-up, 3/4-up grids, portrait pair,
etc. Each section gets a sensible default template (bride solo → full-bleed
portrait; family → grid), overridable per spread.

**Face-safe placement:** the layout engine places photos honoring aspect ratio
and uses the face boxes from Stage A to avoid cropping faces at the bleed or
splitting a face across the gutter.

The layout is held as a **tool-agnostic JSON layout model** (pages → frames →
placed photo + crop transform). This is the source of truth; exporters render
it to specific formats.

---

## Stage E — Export (editable handoff) + retouch round-trip

Export the JSON layout model through an **adapter** to an editable target, with
high-res assets **linked** (not flattened) so design and retouch stay editable.

**Retouch round-trip — recommended sequencing:** lock the album selection
first, then handle retouch as linked-file replacement so layout work isn't
blocked:

1. PhotoFlow exports the editable project with auto-edited **linked** images.
2. Portraits flagged `retouch_needed` are sent to the external editor; the
   retoucher works on the linked files.
3. Updated links flow back into the layout (the project references the same
   paths), and `retouch_status` flips to done.
4. Final export from the layout tool.

This keeps PhotoFlow as the **selection + organization + auto-edit + layout**
brain, and the external editor as the **beauty retouch** hands — each doing what
it's best at.

---

## Suggested build phases

1. **Identity** — embeddings + clustering + the label-once UI; `persons_present`
   on photos; EXIF time + ceremony segmentation. *Unlocks person sheets.*
2. **Auto-edit** — corrections + face-aware crop, non-destructive sidecars.
3. **Section builder** — sections with auto-populate rules + sticky manual
   adjustments.
4. **Layout engine** — album spec (sizes/bleed/DPI), spread templates,
   face-safe placement, JSON layout model.
5. **Export adapter + round-trip** — first editable-handoff format and the
   retouch link workflow.

Phases 1–3 are mostly about data and selection (close to what PhotoFlow already
does well); 4–5 are the new layout/export surface.

## One open decision

**Which editable target?** "Editable handoff" can mean Adobe InDesign (IDML),
Affinity Publisher, a layered PSD, or a dedicated album tool (SmartAlbums /
Fundy / Pixellu, which import organized image folders). The JSON layout model
keeps us tool-agnostic, but the **first export adapter** should target whatever
you actually use — tell me your layout/retouch tools and I'll design Stage E
around them.
