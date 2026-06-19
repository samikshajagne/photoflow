# PhotoFlow Redesign Proposal — Photographer Workflow

**Status:** Design proposal (no implementation). For review.
**Date:** 2026-06-18

## Goals (restated)

1. BestShots = the best photos in the *entire* shoot, not just duplicate-group representatives.
2. Duplicate detection stays an independent process.
3. The unusable bin holds *only* clearly unusable photos.
4. High-quality unique photos are eligible for BestShots.
5. Users can move photos between categories manually, and that sticks.
6. Album generation will later consume BestShots as its primary source.

## Root cause being fixed

Today the four output folders conflate two unrelated things: a photo's **status** (is it a duplicate? is it usable?) and its **quality rank** (how good is it relative to the shoot). BestShots is sourced only from duplicate-group representatives, so quality score never promotes a photo on its own merit and unique photos can't reach BestShots. The redesign separates orthogonal *attributes* (computed per photo) from a single derived *bucket* (what the photographer acts on), and drives BestShots from a global quality ranking.

---

## 1. New category definitions

Keep four exclusive output folders, but redefine what they mean. **Decision: names stay as-is for now** (`Review` and `Blurry`); the redefinition is in meaning, not labels. Suggested future renames noted in parentheses.

**BestShots** — the curated top of the shoot. Usable, deduplicated, highest global quality. *Any* photo is eligible: a unique frame or the representative of a duplicate group. This is the album source.

**Review** (future: *Keepers*) — usable photos that didn't make the top cut but are perfectly deliverable. This is the "everything else that's fine" pool the photographer mines for extra picks. It is no longer a fallthrough junk drawer; it's a positive category.

**Duplicates** — non-representative members of near-duplicate groups. Redundant frames of a moment already represented elsewhere. Independent of quality ranking (Goal 2).

**Blurry** (future: *Rejected*) — *only* clearly unusable photos: severe blur on the subject, or severe exposure failure (near-black / blown-out). Deliberately conservative so the bin stays small and trustworthy (Goal 3). The meaning broadens beyond literal blur to "unusable," but the folder name stays `Blurry` for now.

### Underlying data model (orthogonal attributes, not a single label)

Each photo carries all of these; the bucket is *derived* from them:

- `quality_score` — continuous 0–100, used for ranking.
- `sharpness` — subject-aware (face-region) and global components.
- `exposure` — clipping-based usability + a quality contribution.
- `duplicate_group_id`, `is_group_representative` — from the duplicate stage.
- `usable` — boolean hard gate (conservative).
- `ai_category` — derived bucket (best / keeper / duplicate / rejected).
- `manual_category` — optional user override (Goal 5).
- `effective_category` — `manual_category` if set, else `ai_category`.

Keeping these separate means the UI can say "this is a duplicate **and** a strong frame," and a re-run can re-rank without destroying user edits.

### Bucket resolution order (for the exclusive folder)

1. **Manual override**, if set — always wins.
2. **Duplicate** (non-representative) → Duplicates. Checked before usability so Blurry never contains a redundant frame — Blurry should mean "the only copy of this moment is unusable."
3. **Unusable** → Blurry.
4. **BestShots** — top of the ranked, usable, deduplicated candidate pool.
5. **Review** — everything else usable.

---

## 2. New scoring strategy

Quality becomes a continuous 0–100 score built from subject-aware sub-signals, and is decoupled from the usability gate.

**Sharpness (subject-aware).** When faces are detected, compute Variance-of-Laplacian on the face region(s) and use that as the sharpness signal, not the whole frame. This removes the bokeh-portrait penalty that currently flags tack-sharp subjects as blurry because the soft background dominates the global measure. Fall back to global VoL only when no subject is found. Normalize by first downsampling to `performance.analysis_max_edge_px` (which today is configured but never actually applied), so scores are comparable across resolutions.

**Exposure.** Replace the triangular "distance from mid-gray" brightness curve with a clipping-based metric: penalize the fraction of pixels crushed near 0 or blown near 255, not deviation from 127.5. This stops penalizing legitimately bright high-key frames and white wedding dresses. Contrast remains a secondary exposure signal.

**Subject presence.** Faces still boost the score, optionally weighted by face size (a portrait-scale face counts more than a tiny background face). Hooks left for future signals (eyes-open, composition) as additional weighted sub-scores.

**Combination.** Weighted sum of sub-scores, renormalized by active weights, configurable in `scoring_weights` as today.

**Critical change — two independent thresholds:**

- **Usability floor** (conservative, absolute): below a low subject-sharpness bar *or* with severe exposure clipping → `usable = false` → Rejected. Tuned so only clearly bad photos fail (Goal 3).
- **BestShots selection** (relative + floor): applied to the ranked pool of *usable* photos (see §3).

"Is it usable" (binary, strict) and "how good is it" (continuous, for ranking) are now separate decisions. Today they're entangled in one global threshold, which is why ~41% of delivered frames were flagged blurry.

---

## 3. Top-N vs percentile-based BestShots selection

| Strategy | Pros | Cons |
|---|---|---|
| **Fixed Top-N** | Predictable count; maps cleanly to album page budgets | Wrong across shoot sizes — N=40 is most of a 51-photo set but a sliver of a 1,500-photo wedding |
| **Percentile / proportion** | Scales with shoot size | Relative only — promotes mediocre photos on a weak shoot, drops good ones on a strong shoot |
| **Absolute quality threshold** | Consistent quality bar | Unpredictable count — could be 0 or nearly all |

**Decision (revised — threshold-only, no quota):** the earlier hybrid (top-10%
+ floor + clamp) was replaced. A quota is the wrong model for a photographer: a
top-% cap hides excellent photos on a strong shoot and pads a weak shoot with
mediocre ones. Instead:

- **BestShots = every usable, non-duplicate photo with `quality >= 75`.**
- **No top-percentage, no minimum cap, no maximum cap.** The quality score
  alone decides; the set is as large or small as the shoot deserves.
- **Floor = 75** (raised from 70 now that it is the *only* gate). Hardcoded for
  now; to be tuned on real wedding datasets and **made configurable later**.

Worked examples: a great 900-photo shoot with 180 frames ≥ 75 yields 180
BestShots; an average one with 65 ≥ 75 yields 65; a weak one with 20 ≥ 75
yields 20. In every case the count reflects quality, not an arbitrary target.

Because the cap is gone, the **quality score must be reliable** — this is the
main reason for raising the floor and for the planned dataset tuning. The
diversity guard from §4 remains deferred. Album generation (Goal 6) can still
impose its own page budget downstream without re-introducing a cap here.

**Internal tiers (added):** alongside the four output folders, every analyzed
photo now carries a finer tier for downstream ranking — Hero (90–100),
BestShots (75–89), Review (60–74), Low (<60). The UI still shows BestShots /
Review / Duplicates / Blurry; the tier is metadata for album generation,
highlight reels, and client previews. The BestShots tier boundary (75) mirrors
the selection floor.

---

## 4. How duplicates interact with ranking

Duplicate *detection* remains a standalone stage (Goal 2). It feeds ranking but is not driven by it.

1. Detect near-duplicate groups (unchanged: perceptual hash, Hamming ≤ threshold).
2. **Collapse** each group to one representative = its highest-quality member (subject-aware quality). Non-representatives are flagged `duplicate` and **excluded from the BestShots candidate pool**, so one moment can't occupy multiple BestShots slots.
3. Representatives compete in the global ranking on **equal footing** with unique photos. A representative can win a BestShots slot purely on quality; its duplicates stay in Duplicates regardless of how the representative ranks.
4. **Selection-time diversity guard (soft dedup) — DEFERRED, not in first implementation.** Even frames the detector did *not* call hard duplicates can be near-identical burst shots. At selection, cluster BestShots candidates with a *looser* perceptual-similarity threshold than the duplicate detector and cap how many near-identical frames enter BestShots (e.g., keep the top 1–2 per visual cluster). This prevents eight frames of the same kiss from filling the album source, without touching the hard Duplicates bin. **Planned for a later iteration; the first version ranks without it.**

So there are two layers: the strict **Duplicates folder** (redundant frames, status) and a softer **selection diversity cap** (ranking hygiene). They're separate and independently tunable.

---

## 5. How manual overrides interact with AI selection

**Manual wins and is sticky.** When a user moves a photo, `manual_category` is set and pins `effective_category`. It survives re-scoring and re-runs.

**Identity by content, not path.** Persist overrides keyed by a content hash (plus path as a hint) so renaming or moving the source file doesn't lose the edit. Each override stores `{target_category, timestamp, source: "user"}`. This belongs in the existing `persistence/` layer (e.g., a sidecar JSON or SQLite store), surfaced through `ui_qt/models/photo_index.py`.

**Resolution.** On every analysis run the AI computes `ai_category`; the resolver applies `manual_category` over it. The UI shows both ("AI: Keeper · You: BestShots") so the photographer sees where they diverged from the model.

**Bidirectional.** Overrides can promote (Keeper → BestShots) or demote (BestShots → Keeper/Rejected). A **"Reset to AI"** action clears the override and returns the photo to its computed bucket.

**Edge cases.** Promoting a duplicate to BestShots is allowed but warns ("this frame has a near-twin"). Manual BestShots picks count toward the album source exactly like AI picks. Keep a small audit/log so re-runs that change AI suggestions can flag "AI changed its mind here" without overriding the human.

---

## 6. UI implications

**Category tabs/filters** for BestShots / Keepers / Duplicates / Rejected, each with a live count. BestShots curation becomes the primary task surface (it's the album source).

**Transparency on every thumbnail.** Show the quality score and status badges (duplicate, low-sharpness, clipped exposure, *user-pinned* vs *AI-assigned*). For Rejected, show the reason so the photographer trusts the bin.

**Adjustable cutoff.** A BestShots threshold control (percentage or target-N, per §3) with a live re-count as the photographer drags it — so they can dial the cut to their album budget.

**Move / override.** Drag-and-drop or right-click "Move to…" sets a manual override; pinned photos are visually marked; bulk select + move; "Reset to AI" per-photo and bulk.

**Duplicate group view.** A grouped display showing all members of a near-duplicate cluster, which one is the representative, and a one-click "make this the representative" so the photographer can override the auto-pick.

**Ranking view.** Sort by quality within any category; this is how the photographer audits the BestShots boundary.

**Re-run behavior.** Re-analysis must visibly preserve manual edits and indicate where AI suggestions changed since last run, rather than silently reverting.

---

## Migration / sequencing notes (for later, not part of this proposal's scope)

- Folder names stay `Review`/`Blurry` for now, so `core/organizer.py` constants are unchanged; only their *meaning* shifts. (Future renames to `Keepers`/`Rejected` would be a low-risk constant + label change.)
- The candidate-pool change is the substantive pipeline edit in `core/pipeline.py` (`best_shot_candidates` stops filtering on `if group["duplicates"]` and instead ranks the full usable+deduplicated pool).
- New usability gate and subject-aware sharpness are additions to `core/blur_detector.py` / `core/quality_scorer.py`; the binary blur flag becomes the conservative usability gate, blur's graded contribution stays in the quality score.
- Override persistence is new work in `persistence/` + `ui_qt/models/`.

## Resolved decisions

1. **BestShots selection (revised):** threshold-only — every usable,
   non-duplicate photo with **quality ≥ 75**. **No top-%, no min cap, no max
   cap.** (Supersedes the original top-10% + clamp decision.)
2. **Quality floor:** **75**, hardcoded for now; make configurable and tune on
   real datasets later.
3. **Category names:** keep **"Review"** and **"Blurry"** for now (meaning
   redefined, labels unchanged).
4. **Internal tiers:** Hero (90–100) / BestShots (75–89) / Review (60–74) /
   Low (<60), carried as metadata for later album/highlight features; UI still
   shows the four folders.
5. **Diversity guard:** desired **eventually**, but not yet implemented.

Remaining items intentionally deferred: making the floor/tier thresholds
configurable, dataset tuning of the score, the soft diversity guard, and
clipping-based exposure in the *ranking* score (usability already uses it).
