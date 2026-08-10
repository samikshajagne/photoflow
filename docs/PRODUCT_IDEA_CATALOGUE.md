# PhotoFlow — Product Idea Catalogue

*Drafted 2026-07-30, revised same day with verified findings. A catalogue of possibilities
grouped by theme, not a committed roadmap.
Effort tags are rough: **S** = days, **M** = weeks, **L** = a month or more.*

> **Revision note.** Four previously-open questions are now resolved (Indian passport spec,
> Lightroom Assisted Culling, passport market positioning, pricing direction) — see §8 for
> what's verified and what's still genuinely open. **The passport/ID tool is explicitly
> deprioritised**: it stays as a supporting feature and acquisition wedge, not a core product.
> The strategic conclusion is to build an **end-to-end wedding workflow platform** — cull,
> organize, cluster identities, build albums, client review, export — rather than another
> culling tool.

---

## 0. Read this part first: three things that gate everything else

These aren't features. They're conditions that decide whether the rest of the catalogue is
even sellable. Worth resolving before building anything new.

**0.1 — The InsightFace licence blocks commercial sale. (IN PROGRESS — permissive backend built 2026-07-30, awaiting accuracy verdict)**

> **Update.** Two viable exits confirmed, and the code for the free one is now in place:
> 1. **InsightFace now sells a licence.** Their README (update dated 2025-11-24) directs
>    commercial users of the open-sourced recognition packs, `buffalo_l` included, to
>    `recognition-oss-pack@insightface.ai`. Zero accuracy loss, costs money, needs no code
>    change. Verbatim from the README: *"The training data containing the annotation (and the
>    models trained with these data) are available for non-commercial research purposes only."*
> 2. **A permissively-licensed local swap now exists in-tree:** `core/sface_backend.py` uses
>    **SFace** (Apache-2.0) for recognition + **YuNet** (MIT) for in-crop alignment, both from
>    `opencv_zoo`, via OpenCV's own ONNX support — so no new dependency and nothing leaves the
>    machine. It plugs into the same `EmbedBackend` callable seam, so switching is a one-line
>    change at `core/album/orchestrator.py:530`.
>
> **Decide with data, not published benchmarks:** run `scripts/benchmark_embedders.py
> --labelled <folder-per-person>` on real wedding photos. It reports same-person vs
> different-person distance **separation**, the best-separating threshold, end-to-end
> clustering purity, and ms/face. If SFace holds up on your photos, the blocker is solved for
> free; if not, email InsightFace.
>
> **Trap to remember:** the clustering threshold is a property of the *embedding model*.
> `DEFAULT_DISTANCE_MAX = 0.55` was tuned for ArcFace's 512-d vectors; SFace emits 128-d
> vectors with a different distance distribution, so `core/person_cluster.py` now carries
> per-backend thresholds (`DISTANCE_MAX_BY_BACKEND` / `distance_max_for_backend()`) with
> `SFACE_DISTANCE_MAX` marked **provisional** until the benchmark runs. Swapping the embedder
> without re-tuning wouldn't crash — it would just quietly merge different guests into one
> person, or split one person into many.
>
> Also checked and cleared: dlib's `shape_predictor_68_face_landmarks` weights forbid
> commercial use, but this project doesn't use dlib at all. ONNX Model Zoo's ArcFace claims
> Apache-2.0 while actually being the InsightFace model trained on research-only MS-Celeb-1M —
> **avoid it**, the licence claim is contradictory.
>
> Original assessment follows.


`core/insightface_backend.py` uses the `buffalo_l` pack (ArcFace `w600k_r50` + SCRFD).
InsightFace's pretrained packs are released for **non-commercial research use only**. Face
recognition is the engine behind person clustering and "label people," so this isn't a
peripheral dependency — it's the core of the album product, and it currently cannot legally
ship in a paid product. Options, roughly in order of attractiveness:
- Swap to a permissively-licensed recognition model (look for MIT/Apache-2.0 ArcFace
  reimplementations trained on permissively-licensed data, or licence a commercial SDK).
- Licence InsightFace/ArcFace commercially if the rights holder offers it.
- Train or fine-tune an embedding model in-house on licensed data (expensive, slow).
- Ship recognition as an *optional* user-installed component and never distribute the
  weights — legally grey and probably not defensible for a paid product; get real advice.
Note the precedent already set with cutouts: BiRefNet-general was chosen specifically
*because* it's MIT-licensed. Apply that same filter to every model in the stack, and write
a short `LICENSES.md` recording the licence of each one.

**0.2 — Per-photo GPT-4o calls don't survive contact with a real Indian wedding. (M)**
`core/vision_brain.py:analyze()` calls GPT-4o **once per photo**. A FilterPixel customer
publicly cites ~20,000 photos per wedding, which matches multi-day Indian event volumes.
At that scale a per-image cloud call is three problems at once: cost per job that scales
linearly with volume, a hard internet dependency on a deadline, and guest faces leaving the
studio's machine — which is exactly the privacy objection the repo's own
`API_vs_LOCAL_ANALYSIS.md` argues against. Directions:
- Run vision only on a **sampled subset** (one frame per cluster/scene, not every frame),
  then propagate labels across near-duplicates. Likely a 20–100× call reduction.
- Move scene labelling to a **local** model and keep the cloud call as an optional
  "better labels" upgrade.
- Cache aggressively by perceptual hash so re-runs and near-dupes never re-bill.
- Show the studio a **cost estimate before the run** if any cloud path is enabled.

**The target split.** Everything mechanical stays local; only genuine language understanding
justifies an LLM. This keeps cost near zero, works offline, and keeps guest faces on the
studio's machine:

| Task | Where it belongs |
| --- | --- |
| Face detection | Local |
| Blur / sharpness detection | Local |
| Exposure assessment | Local |
| Duplicate & burst detection | Local |
| Identity clustering | Local |
| Aesthetic scoring | Local |
| Semantic captions, album storytelling | LLM (cloud, or a capable local vision model) |

**Prove it before optimising it.** Take a real ~5,000-image wedding set and measure local vs
GPT-4o on: label accuracy, hallucination rate, cost, latency, and blind user preference.
Keep GPT-4o **only** where it produces an improvement users notice and would pay for.
Building out the cloud path before running this experiment risks optimising something that
shouldn't exist.

**0.3 — ~~The test suite has 13 known failures.~~ DONE 2026-07-30 — suite is green (765 passed).**
Triaged into three genuinely different causes, which is why this was worth doing before
anything else — one of them was a live user-facing bug hiding behind "stale tests":
- **7 tests (pipeline, pipeline_quality): environment, not code.** With MediaPipe absent,
  face detection *fails* for every image, and the pipeline then deliberately bypasses face
  scoring ("so BestShots selection is not arbitrarily emptied"). That bypass moves photos
  between BestShots and Review, so the assertions depended on whether MediaPipe happened to
  be installed. Fixed by injecting a `StubFaceDetector` (new `tests/conftest.py`) that
  *succeeds* and reports zero faces — which is what a real detector does on these synthetic
  fixtures. Now deterministic on any machine, and testing the intended path rather than the
  all-detection-failed fallback.
- **3 tests (auto-edit, album layout): stale expectations.** The auto-edit "neutral" fixture
  drew *independent* per-channel noise, so its luma std was 0.143 (not the 0.215 intended) —
  averaging independent channels cancels variance — meaning the image really was flat and the
  engine was right to boost contrast. Fixed the fixture (grayscale noise replicated across
  channels) and added a companion test asserting genuinely-flat images *do* still get boosted.
  The layout tests asserted global photo order and all-wide frames; both contradict the
  intentional `_assign_by_orientation` aspect-matching. Rewritten to assert what matters
  (nothing lost/duplicated, chronology preserved *across* spreads, frames skew wide and
  respond to aspect).
- **3 tests (layout_select): a real, user-visible bug.** `select()` computed
  photos-per-spread purely from the page budget and never used the per-kind policy or
  `density` — leaving `_per_spread`/`_scaled()` computed but dead. The album settings dialog
  exposes density as a **"Photos per spread"** dropdown, so users could change it and nothing
  happened. Fixed with a new `_per_spread_for()` that reconciles all three intents: heroes
  stay single, otherwise the density-scaled per-kind count is used, raised to the page budget
  only when packing is needed. Verified: 36 photos now give 18/12/8 spreads for
  spacious/balanced/dense, and 600 photos still cap at 24 spreads.

**Lesson worth keeping:** "known failing tests" was concealing a genuine product bug. A red
suite doesn't just risk hiding *future* regressions — it was already hiding a present one.

---

## 1. The strategic wedge (why PhotoFlow can win)

Worth stating plainly, because it shapes which ideas below matter most.

The AI culling/editing market (**Aftershoot** ~$96–720/yr, **Imagen** ~$7/mo PAYG to
$179/mo, **Narrative** $10–60/mo, **Evoto** credit-per-export, **FilterPixel**) is crowded
and well funded. But **none of them produce a printed album.** They hand you selects and
edits, then stop.

The album market (**SmartAlbums**, **Fundy Designer**, **Album DS** ~€45/yr) does layout —
but across all three, "automatic" means *template matching driven by photo order, count,
orientation and EXIF timestamps.* **None of them advertise face-aware or event-aware
automatic layout.** Manual drag-and-drop is still the polish step everywhere.

So the gap is specific and real: **nobody automates the path from 20,000 raw frames to a
print-ready, person-balanced, ritual-aware album.** PhotoFlow already spans cull → organize
→ cluster people → build spreads → export print files, locally, in one app. That combination
is the moat. Most ideas below are worth judging by whether they widen it.

Two other observations from the research worth keeping in view:
- **Nobody publishes INR pricing, local payment rails, or GST invoicing.** For Indian
  studios that's genuine adoption friction, and it's cheap for PhotoFlow to fix.
- Aftershoot is itself an India-registered company, so "Indian-built" alone is not a
  differentiator. The workflow coverage is.

---

## 2. Smart / AI features

### 2.1 Culling and selection
- **Ritual/event-aware structuring (L).** Classify frames into haldi / mehendi / sangeet /
  baraat / pheras / vidaai / reception, then guarantee coverage per event. Directly attacks
  the gap no competitor addresses. Probably the single most differentiating idea in this doc.
- **Per-person coverage guarantees (M).** Not just "filter by face" (which Narrative and
  Aftershoot already do) but a *contract*: "every album gets ≥N frames of the bride's
  parents, ≥M of each sibling." Face clustering already exists; this turns it into a
  deliverable promise.
- **Expression and eyes-open scoring (M).** The most common complaint about Aftershoot is
  that it misses subtle expressions. A studio-tunable "smile/eyes/blink" score with a
  visible *reason* per frame (FilterPixel's "DeepCull" shows a reason — a good pattern to
  copy) would land well.
- **Near-duplicate burst collapsing (S–M).** Group bursts, pick the sharpest/best-expression
  representative, keep the rest one click away. Partially present via perceptual hashing;
  worth hardening since burst-grouping errors are a known competitor weak spot.
- **"Why was this rejected?" explainability (S).** Every automated decision shows its
  reason. Cheap to add, disproportionately builds trust, and reduces support load.

### 2.2 Album intelligence
- ~~**Face-aware layout (M).**~~ ✅ **first pass done 2026-08-05** — see §9.1. Slot matching
  now measures what a slot's shape would do to a photo's faces, and the layout engine keeps
  crowded photos out of the smallest cells. Still open: sizing slots *around* faces (rather
  than choosing among fixed slots), and the underlying detector-miss problem — when detection
  finds no face there is nothing to protect, and no amount of layout logic fixes that.
- ~~**Narrative pacing (M).**~~ ✅ **done 2026-08-05** — see §9.1. Spread density now varies
  to a deliberate rhythm without changing the album's length. Still open: choosing *which*
  photo earns each hero spread, which needs quality scores the layout engine doesn't get yet.
- **Colour-story-aware sequencing (M).** Group spreads so adjacent pages don't clash; the
  dominant-colour data already exists in the vision brain.
- **Learn the studio's taste (L).** Record which auto-layouts the studio overrides, and bias
  future layouts accordingly. Imagen's "Personal AI Profile" is the proven analogue, applied
  to *layout* rather than colour — where nobody is doing it.
- **Auto-generated album variants (M).** Produce 2–3 complete alternative designs and let
  the studio pick, rather than one take-it-or-tweak-it output.

### 2.3 Retouching
- **Extend beautify to the album pipeline (M).** The passport tool now has skin smoothing,
  colour correction, background whitening and teeth/eye whitening. The album path only has
  the gentle auto-edit. Same engine, much bigger surface.
- **Real landmark-based retouching (M).** Current beautify uses a *fixed* canonical face box
  because passport crops are predictable. Album photos aren't, so this needs actual
  landmarks (MediaPipe Face Mesh) to place effects per face.
- **Per-face treatment in group photos (M).** Evoto's per-face retouching is a selling
  point; group shots are the Indian wedding norm, so this matters more here than elsewhere.
- **Print-specific skin/colour polish (M).** Nobody in the cull/edit space targets *print*
  output specifically. Soft-proofing for a specific lab's paper profile would be a genuinely
  novel, printer-aware feature.

### 2.4 Government & Visa photo tool (deprioritised — supporting feature, not core)

**Rename the concept.** At normal Passport Seva Kendra appointments the photo is **captured
digitally on site**, so "Passport Photo Generator" describes a job that largely doesn't exist
in-country. The real demand is everything *around* it, which argues for positioning this as a
**"Government & Visa Photo Generator"**: overseas/VFS renewals, postal applications, visa
applications, OCI, PAN, Aadhaar, driving licence, plus job/student applications and general
walk-in ID photos. Same code, honest scope, larger addressable use.

Verified spec now in hand (Passport Seva instruction booklet): **45 × 35 mm**, plain white
background, colour, recent, full frontal face, eyes open, head centred, **both ears visible**,
good-quality photo paper, no shadows, no dark glasses unless medically required. The tool's
existing 30×35 mm default is the studio's own habit, not the official size — worth surfacing
45×35 mm as a named "India — Passport (official)" preset alongside it.

Ideas, kept for later rather than actively pursued:
- **Compliance validation (M).** Verify and explain rather than just crop: "will be rejected —
  head is 62% of frame height, needs 70–80%." ICAO Doc 9303 Part 3 is machine-checkable
  (45×35 mm, inter-eye distance ≥10 mm, crown-to-chin 70–80% of Zone V's longest dimension,
  within 6 months) and the standard *itself* recommends gauge overlays for head size and roll.
  Consumer tools are shallow here — one validator lists "head size 32 mm" for every country
  including both India and the US, a generic default dressed up as a per-country rule.
  Checking "both ears visible" and "no dark glasses" is also now explicitly in scope for India.
- **Per-country preset packs (S).** India (official 45×35), US, UK, Schengen, Canada, China,
  Australia, Gulf states. Data, not algorithms.
- **Auto glasses-glare / head-tilt / shadow detection (M).** The common rejection causes.
- **Uniform background enforcement (M).** Detect a non-compliant backdrop and flatten it;
  groundwork exists in the current background-whitening code.
- **Batch mode (S–M).** School/corporate ID jobs = 200 people, one sheet each. Currently
  one-sheet-at-a-time. The most commercially useful item in this section.

---

## 3. Speed and reliability

- **Never do heavy work on the UI thread (M).** Recently learned the hard way: beautify ran
  a BiRefNet inference per slider tick and took the whole machine down. The fixes applied
  there (opt-in heavy models, cached downscaled preview sources, debounced recompute) should
  become a **house rule**, and ideally an architectural one — a single background-worker
  abstraction with progress + cancel, used by every long operation.
- **Two-resolution pipeline everywhere (M).** Proxy resolution for all interaction, full
  resolution only on export. Already true in the passport preview; should be universal.
- **GPU acceleration, optional and safe (M).** onnxruntime GPU providers for
  detection/embedding/matting, with automatic CPU fallback. Big wins at 20k-photo scale, but
  a known source of driver-level crashes — must be defensive and switchable.
- **Resumable jobs (M).** A 20,000-photo run must survive a crash, a reboot, or a closed
  laptop and pick up where it stopped. Checkpoint per stage.
- **Incremental re-analysis (S–M).** Adding 200 photos to an analyzed folder should process
  200, not 20,200.
- **Multi-process batch stages (M).** Detection/embedding are embarrassingly parallel; use
  all cores with a bounded worker pool.
- **Memory ceilings (M).** Streaming/tiled processing so a 24MP × 20k job never balloons.
  Cap decoded-image caches explicitly.
- **Crash telemetry, opt-in and local-first (S).** Even just a local crash log the studio can
  attach to a support mail. Right now a crash is invisible to you.
- **A real performance benchmark in CI (S).** Lock in per-stage timings on a fixed synthetic
  set so regressions like the slider incident get caught by a test, not by the user.
- **Preflight hardware/dependency check (S).** On startup: is MediaPipe present, is there
  enough disk and RAM for this job, is a GPU usable? Fail loudly and early instead of
  degrading silently mid-run.

---

## 4. Commercial readiness

- **Resolve model licensing (L).** See §0.1. Nothing else in this section matters until it's
  done. Produce a per-model `LICENSES.md` as the artefact.
- **Windows installer + code signing (M).** A signed installer, bundled dependencies, no
  `pip install` steps for the end user. Unsigned binaries get SmartScreen warnings — Album DS
  visibly suffers from exactly this.
- **Licensing/activation (M).** Offline-friendly (studios can't depend on connectivity),
  machine-bound with a self-service seat move. Note the competitor norms: 2 machines per
  seat is standard (SmartAlbums, Album DS, Photo Mechanic).
- **INR pricing, GST invoices, UPI/local payment rails (S–M).** *No competitor does this.*
  Cheap, unglamorous, and a direct advantage in the target market.
- **Pricing model choice (S to decide, M to build).** The field offers three templates:
  per-image credits (Evoto — punishing at 20k/wedding, and a reason to avoid it), flat
  unlimited subscription (Aftershoot/Narrative), or perpetual + paid upgrades (Photo
  Mechanic, Album DS ~€45/yr). For volume-heavy Indian studios, **flat/perpetual reads as
  the differentiator**, and "we never charge per photo" is a marketing line in itself.
  Observed market behaviour: small studios strongly prefer **one-time purchases**, avoid
  expensive subscriptions, are very price sensitive, and piracy is a real competitive
  alternative; large studios will pay when hours are saved and care more about reliability
  and throughput. A starting shape to test — **free tier**, **₹999–1,999 one-time starter**,
  **₹4,999–9,999 Pro**, custom enterprise. Treat these as a hypothesis: **interview 20–30
  photographers before locking pricing.** Note that the piracy reality argues for cheap-enough
  paid tiers plus a genuinely useful free tier over aggressive DRM.
- **Studio/team primitives (M–L).** A genuine competitor weakness: Narrative caps at 1–4
  users, Imagen's flat plan is *solo-only*, Photo Mechanic is 1 user/2 machines. Multi-seat
  studios with assistant editors are underserved — assignment, QC queues, per-job costing.
- **Trial mode (S).** Watermarked exports, full features, time-limited. Universal in this
  market.
- **In-app onboarding on real sample data (S–M).** Ship a small sample wedding set so a first
  run produces something impressive in minutes.
- **Auto-update (M).** Otherwise every bugfix requires manual reinstall by non-technical users.
- **Documentation and video walkthroughs (S–M, ongoing).** In Hindi as well as English —
  none of the competitors localise for India (SmartAlbums ships English/PT/ES/IT/DE/FR only).
- **Data-handling statement (S).** Write down exactly what leaves the machine and when.
  If the local-first story is real, it's a sales asset — make it explicit and prominent.

---

## 5. Differentiation and market

- **Own "raw to printed album, locally, no per-photo fees."** That sentence is the product.
  It is simultaneously true, valuable, and unavailable from any competitor.
- **Adobe shipped Assisted Culling — so don't compete on culling alone.** Lightroom and
  Lightroom Classic now do AI culling: subject and eye sharpness, eyes open, exposure
  problems, misfires, document/receipt detection, batch organisation, auto select/reject. It
  doesn't kill standalone culling, it raises the floor — technical culling is now a commodity
  feature bundled with software most studios already own. What Adobe does *not* do:
  storytelling, wedding-sequence construction, duplicate grouping across tens of thousands of
  frames, album layout generation, client selection, print workflow, or anything
  Indian-wedding-specific. Every one of those is a better place to invest than a sharper
  blur detector. This is the strongest single argument for the end-to-end platform framing.
- **Hands-on competitive teardown (S, do this early).** Marketing pages were a weak source.
  Download trials of **Album Xpress**, **DgFlick**, **SmartAlbums** and **Fundy Designer**,
  watch 3–4 real YouTube workflows each, and write down precisely where photographers waste
  time. That will generate better product ideas than any amount of further desk research —
  and DgFlick and Album Xpress are the closest direct competitors to PhotoFlow's album
  workflow in the Indian market.
- **Indian wedding fluency as a feature, not a locale (M).** Ritual vocabulary, multi-day
  multi-venue structure, very large family groups, 20k-frame volumes, printed albums as the
  primary deliverable. Global tools treat all of this as an edge case.
- **Lab/printer integrations (M).** SmartAlbums and Fundy each tout 170+ album vendor specs
  and misprint guarantees; that's table stakes to compete on album output. Start with the
  labs the studio actually uses, and support Canvera-class Indian photobook printers.
- **Client proofing that feeds back into the album (M–L).** The research found proofing is
  disconnected from culling AI everywhere, and client selections never drive page counts.
  Closing that loop — clients pick, album rebuilds itself — is a clean unsolved problem.
- **Multi-shooter merge with clock-skew correction (M).** 5–8 bodies per Indian wedding, and
  nobody handles the merge/dedupe/timeline-alignment problem. Unglamorous and very real.
- **Passport photos as the daily-revenue wedge (S–M).** Weddings are seasonal; ID photos are
  everyday walk-in cash. It's also a far shorter sales cycle and a natural way to get
  PhotoFlow installed in a studio, after which the album product is an upsell. Worth
  treating as a customer-acquisition channel rather than a side feature.
- **Reprint and re-order workflow (M).** Studios re-sell the same album/photos for years;
  nobody supports it well.
- **Adjacent deliverables (S–M each).** Save-the-date and invitation cards, thank-you cards,
  wall-art/framing previews, slideshow video, social-crop exports. Fundy already bundles
  wall art and IPS/sales tooling — evidence studios buy these.
- **In-person-sales (IPS) view (M).** A clean full-screen "show the couple their album" mode.
  Fundy monetises this specifically.

---

## 6. Quick wins vs big bets

**Highest value per unit of effort:**
- Fix the 13 failing tests (S–M)
- Hands-on competitor teardown (S) — trials + workflow videos
- "Why was this rejected/chosen?" explainability (S)
- INR pricing / GST invoicing (S–M)
- Preflight dependency + hardware check (S)
- Performance benchmark in CI (S)
- Batch mode for the ID-photo tool (S–M) — the one passport item with clear revenue

**Big bets, in rough order of strategic payoff:**
1. Ritual/event-aware album structuring (L) — the thing nobody else does
2. Resolve model licensing (L) — the thing that makes selling legal
3. Learn-the-studio's-taste layout (L)
4. Proofing that feeds back into album generation (M–L)
5. Multi-seat studio workflow (M–L)
6. ID-photo compliance validation against ICAO/per-country rules (M) — deprioritised

---

## 8. Status of the open questions

**Resolved.**
- ✅ **Indian passport photo spec — verified** against the Passport Seva instruction booklet:
  45 × 35 mm, plain white background, colour, recent, full frontal face, eyes open, head
  centred, both ears visible, good photo paper, no shadows, no dark glasses unless medically
  required. Source: `passportindia.gov.in/AppOnlineProject/pdf/ApplicationformInstructionBooklet-V3.0.pdf`
- ✅ **Passport market positioning — resolved.** PSK appointments capture the photo on site, so
  this is a *supporting* feature, not a core product; reposition as a Government & Visa photo
  generator (§2.4) and deprioritise.
- ✅ **Lightroom Assisted Culling — confirmed shipped** in Lightroom and Lightroom Classic
  (subject/eye sharpness, eyes open, exposure issues, misfires, document detection, batch
  organisation, auto select/reject), launched as Early Access and since expanded. Sources:
  `helpx.adobe.com/lightroom-classic/desktop/organize-photos-in-lightroom-classic/assisted-culling.html`,
  `adobe.com/learn/lightroom-cc/web/ai-assisted-culling-lightroom`. Implication in §5:
  compete on the workflow, not the cull.
- ✅ **Pricing direction — decided enough to test.** Indian small studios prefer one-time
  purchases and are highly price sensitive (with piracy a live alternative); large studios pay
  for saved hours and throughput. Candidate ladder in §4, to be validated by interviews.

**Still genuinely open.**
- ⚠️ **Hands-on competitor research not yet done.** Album Xpress, DgFlick, SmartAlbums, Fundy
  Designer — trials and real workflow videos, per §5. This is the highest-value remaining
  research task and can't be done from marketing pages.
- ⚠️ **Pricing needs 20–30 photographer interviews** before anything is locked.
- ⚠️ **GPT-4o vs local benchmark not yet run** — design is in §0.2; run it on a real ~5,000
  image wedding set before investing further in the cloud path.
- ⚠️ **Exact SmartAlbums / Fundy price points unverified** (JS-rendered checkouts). Album DS
  (~€45/yr) and the Aftershoot / Imagen / Narrative / Evoto / Photo Mechanic figures are from
  their own pages.

---

## 9. Fix queue (working order)

Agreed approach: work through these one at a time, passport tool deprioritised.

1. ~~**Green the test suite**~~ (§0.3) — ✅ **done**, 765 passed / 0 failed. Uncovered and fixed
   a real bug: the density setting did nothing.
2. **Resolve model licensing** (§0.1) — permissive SFace backend + benchmark harness built;
   ← *awaiting your accuracy run on real photos to pick the path*
3. **Cut per-photo cloud calls** (§0.2) — benchmark first, then sample/localise.
4. **Reliability rules as architecture** (§3) — background worker + two-resolution pipeline.
5. **Album quality: face-aware layout and narrative pacing** (§2.2) — the differentiator.
   ✅ **first pass done 2026-08-05.** Both halves landed; see §9.1 for what was built
   and what deliberately wasn't.

### 9.1 What the album-quality pass actually changed

**Narrative pacing — new `core/album/pacing.py`.** Spreads no longer all carry the
same number of photos. A *rhythm* is a short cycle of weights whose mean is exactly
1.0, so dense beats pay for sparse ones and — this is the part that made it safe to
turn on by default — **the spread count comes out identical to uniform packing.**
Pacing therefore can't disturb the density setting or the page budget; the two are
genuinely independent controls. At 36 photos / 3 per spread the editorial rhythm
gives `[4, 3, 4, 1, 4, 3, 4, 1, …]` against uniform's twelve 3s. Exposed in the album
settings dialog as **Pacing** (editorial / gentle / uniform), defaulting to editorial.

Pacing stands down — reverting to uniform — for hero sections, sections under three
spreads, and albums already packed to the cap. In all three a varying count reads as
a mistake rather than a decision.

**Face-aware layout — two separate fixes, because there were two separate bugs.**
The existing face-safe *cropping* was already good; what was missing was everything
upstream of it:

- `face_crop_loss()` (in `facecrop.py`) measures the fraction of face area a slot's
  shape would slice away, and now feeds the slot matcher as a heavily-weighted
  penalty. This catches a case the previous scoring structurally could not see:
  `_aspect_match` compares *shapes*, so a wide group photo and a wide slot looked
  like a good pairing even when the guests spanned wider than the slot could hold.
  Only the face boxes' actual extent reveals that, so it had to be scored
  separately. Required adding `face_boxes` to `PhotoContent`, which carried
  centroids but not extents.
- The layout engine's photo→frame assignment now considers **crowding**, so a
  fifteen-person family group stops landing in the smallest cell on the spread.
  Weighted as a nudge, not a veto — a wrong-shaped cell is visible on every spread,
  whereas a slightly small group photo is only a missed opportunity.

**A calibration worth remembering.** The face-safety penalty is set at 150, above the
120 that every positive sub-score can contribute at once, so a slot that would destroy
the faces entirely is disqualifying however well everything else matches. The
reasoning generalises: the other sub-scores are *generic labels* about what a slot is
for, while face loss is a *measurement* of what this slot would do to this photo, and
the specific measurement should outrank the generic label.

**Also caught, and worth flagging as a recurring failure mode.** The first "gentle"
rhythm was a no-op. Its weights (1.15 / 1.0 / 0.85) were close enough to 1.0 that at
the three-or-four-photos-per-spread real albums use, every one rounded to the same
integer — a dropdown option that changed nothing. This is the *same* bug class as the
density setting that silently did nothing (§0.3), found twice now in the same
subsystem: **a setting whose effect is quantised can be swallowed by rounding.** There
is now a test asserting every offered rhythm differs from uniform at 3, 4 and 5 photos
per spread, and one asserting every dropdown value is a rhythm the selector actually
recognises.

**Not done, deliberately.** Pacing varies *how many* photos a spread gets, never
*which* ones — order is untouched, so chronology is preserved exactly. Choosing a
genuinely good photo for each hero beat needs quality scores the layout engine does
not currently receive, and would mean local reordering. That is the obvious next step
and a bigger decision than it looks.
