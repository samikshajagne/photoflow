# PhotoFlow — Phase 3 & 4 Detailed Implementation Roadmap

**Purpose:** Transform album layouts from grid-based (Sheet 2) to composition-aware, face-safe (Sheet 1 quality)  
**Status:** Planning only — no code changes yet  
**Last Updated:** 2026-07-16

---

## Executive Overview

### What We're Building

| Phase | Focus | Output Quality |
|-------|-------|---|
| **Phase 3** | Face-aware cropping + intelligent slot matching + cutout masks | Editorial album look (Sheet 1) |
| **Phase 4** | Variable aspect ratios + smart placement + decorative assets | Professional template library (Sheet 1+) |

### Current State → Target State

**Current (MVP):**
- Fixed grid layouts (3 photos per spread, all same size)
- No composition awareness → faces cut in half, wrong aspect ratios
- Rectangular photo frames only
- Deterministic slot filling (just grab "next best photo")

**Target (Phase 3):**
- Face detection prevents cutting faces
- Photos matched intelligently to slots based on composition
- Artistic cutout frames (circles, ovals, diamonds)
- Photographer reviews placement before export

**Target (Phase 4):**
- Variable slot sizes per spread (not locked grid)
- AI suggests optimal photo placement per spread
- Themed decorative backgrounds + asset library
- Cover designer for couple names + date

---

## Phase 3: Composition-Aware Layout (8–10 weeks)

### Phase 3 Goal
*Ship albums where no faces are cut, photos are intelligently matched to slots, and cutout masks create editorial visual impact.*

### Phase 3 Workstreams

---

## Workstream 3.1: Face-Aware Intelligent Cropping

### 3.1.1 Crop Engine Architecture

**File:** `core/crop_engine.py` (NEW)

**Purpose:** Given a photo + face bboxes + target slot dimensions, calculate the best crop window that:
1. Keeps all detected faces fully visible (never cut)
2. Fills the target slot dimensions
3. Prioritizes face-first framing (faces centered in crop)

**Inputs:**
- `photo_path` (str) — path to image file
- `face_bboxes` (list of dicts) — `[{"x": 10, "y": 20, "w": 100, "h": 120}, ...]` from face detection
- `target_width` (int) — desired crop width (in pixels)
- `target_height` (int) — desired crop height (in pixels)
- `mode` (str) — "fill" (zoom to fill) or "fit" (letter-box)

**Output:**
- `crop_window` (dict) — `{"x": 0, "y": 0, "w": 640, "h": 480}` crop coordinates
- `scale_factor` (float) — zoom applied (1.0 = no zoom)
- `viable` (bool) — whether crop is possible without extreme distortion

**Key Methods:**

```
calculate_face_safe_crop(photo, face_bboxes, target_w, target_h, mode):
  1. Load image dimensions
  2. Calculate bounding box of ALL faces combined
  3. Add 30px padding around face bbox (context/neck/shoulders)
  4. Calculate crop window that fits target_w/target_h while including padded face bbox
  5. If photo aspect ratio differs from target, choose: zoom + center OR shift to preserve faces
  6. Return crop_window, scale_factor, viability score
  
expand_crop_for_context(photo, face_bbox, padding=30):
  1. Expand face bbox by padding pixels in all directions
  2. Ensure expanded box stays within image bounds
  3. Return padded bbox
  
check_crop_viability(crop_window, face_bboxes, target_aspect):
  1. Verify all faces fit in crop without cutting
  2. Calculate how much distortion/scaling is needed
  3. Return viability score 0–100 (100 = perfect, 0 = impossible)
```

**Dependencies:**
- Requires face detection to have run already (pipeline caches these)
- Uses PIL/OpenCV for image loading + dimension calculations

**Testing:**
- Unit test: Face bbox fully inside crop window ✅
- Unit test: Crop fills target dimensions ✅
- Unit test: Multi-face photos handled correctly ✅
- Unit test: Edge cases (face at image edge, very small face, very large face) ✅

---

### 3.1.2 Crop Integration into Pipeline

**File:** `core/pipeline.py` (MODIFY)

**What changes:**
- After quality scoring, run crop engine on each photo
- Store crop metadata in `AnalysisCache` alongside face bboxes

**New pipeline stage:**
```
pipeline.py → quality_scorer → [NEW: crop_calculator] → cache result
```

**New cache structure:**
```
AnalysisCache:
  photo_id:
    faces: [bbox1, bbox2, ...]
    quality_score: 0–100
    [NEW] crop_suggestions: {
      portrait_slot: {crop_window: {...}, viability: 95},
      landscape_slot: {crop_window: {...}, viability: 88},
      square_slot: {crop_window: {...}, viability: 72}
    }
```

**Effort:** 1–2 days

---

### 3.1.3 Crop Application in Album Generation

**File:** `core/album/orchestrator.py` (MODIFY)

**What changes:**
- When filling a spread slot, read pre-calculated crop from cache
- Apply crop before rendering photo into PSD/PNG

**New method:**
```
apply_intelligent_crop(photo_path, slot_type):
  1. Load crop suggestion from cache for slot_type
  2. Load image
  3. Apply crop coordinates
  4. If slot needs rotation (e.g., portrait photo in landscape slot), decide: 
     a) Rotate + apply crop (may cut faces) OR
     b) Pan/zoom to fit without rotation (safer)
  5. Return cropped image
```

**Effort:** 1–2 days

---

## Workstream 3.2: Subject-Aware Slot Matching

### 3.2.1 Photo Content Analyzer

**File:** `core/content_analyzer.py` (NEW)

**Purpose:** Analyze each photo to determine what "type" it is (portrait, group, detail, landscape, etc.)

**Analysis outputs per photo:**

```python
PhotoContent = {
    face_count: int,                    # 0, 1, 2–5, 5+
    dominant_face_size: float,          # % of image occupied by largest face
    face_positions: [x, y, x, y, ...],  # centroid of each face
    composition_type: str,              # "portrait", "group", "detail", "wide", "full_body"
    subject_isolation: float,           # 0–100: how isolated is main subject from background
    orientation: str,                   # "portrait", "landscape", "square"
    aspect_ratio: float,                # width/height
}
```

**Methods:**

```
analyze_photo_composition(photo_path, face_bboxes):
  1. Load image
  2. Count faces in face_bboxes
  3. Calculate % of image occupied by faces (avg face area / image area)
  4. Determine face positions (left/center/right, top/center/bottom)
  5. Classify composition type based on:
     - 1 face + large face % → "portrait"
     - 2–5 faces + centered → "group"
     - Face small + background prominent → "detail" or "landscape"
     - Full body visible → "full_body"
  6. Calculate subject isolation (edge detection around main face region)
  7. Return PhotoContent dict

classify_composition_type(face_count, face_coverage_pct, face_centroid):
  1. If face_count == 1 and face_coverage > 40% → "portrait"
  2. If face_count == 1 and face_coverage 20–40% → "detail"
  3. If face_count 2–5 and centered → "group"
  4. If face_count 5+ → "large_group"
  5. If face_count == 0 → "landscape" or "still_life"
  6. If face visible but not prominent → "environmental"
```

**Dependencies:**
- Face bboxes from pipeline cache
- PIL/OpenCV for image analysis

**Testing:**
- Unit test: Portrait photo classified correctly ✅
- Unit test: Group shot classified correctly ✅
- Unit test: Detail/close-up classified correctly ✅
- Integration test: On sample wedding shoot, verify classifications align with human judgment ✅

**Effort:** 2–3 days

---

### 3.2.2 Slot Type Definition & Schema

**File:** `core/album/slot_schema.py` (NEW)

**Purpose:** Define all possible slot types (frame shapes, sizes, aspect ratios)

**Slot schema structure:**

```python
SlotType = {
    name: str,                    # "portrait_large", "group_landscape", "detail_square"
    aspect_ratio: float,          # 3:4 (0.75), 16:9 (1.78), 1:1 (1.0)
    width_px: int, height_px: int,  # absolute size on 5400×3600 spread
    ideal_composition: [str],     # ["portrait", "full_body"] → what photos fit best
    ideal_face_count: (int, int), # (1, 2) → best with 1–2 faces
    frame_style: str,             # "rectangle", "rounded", "ornate_gold" (Phase 4+)
    position_on_spread: (x, y),   # top-left corner on spread canvas
    padding: dict,                # {top, bottom, left, right} margin pixels
    can_rotate: bool,             # whether photo can be rotated if aspect ratio doesn't match
}
```

**Pre-built slot types (Phase 3):**

```python
SLOT_TYPES = {
    "portrait_large": {
        aspect_ratio: 0.75,   # 3:4
        width_px: 1200, height_px: 1600,
        ideal_composition: ["portrait", "full_body"],
        ideal_face_count: (1, 2),
        position_on_spread: (600, 300),
    },
    "portrait_small": {
        aspect_ratio: 0.75,
        width_px: 800, height_px: 1066,
        ideal_composition: ["portrait"],
        ideal_face_count: (1, 1),
        position_on_spread: (300, 300),
    },
    "landscape_wide": {
        aspect_ratio: 1.78,   # 16:9
        width_px: 1800, height_px: 1010,
        ideal_composition: ["group", "landscape"],
        ideal_face_count: (2, 5),
        position_on_spread: (300, 300),
    },
    "group_square": {
        aspect_ratio: 1.0,    # 1:1
        width_px: 1000, height_px: 1000,
        ideal_composition: ["group"],
        ideal_face_count: (2, 5),
        position_on_spread: (400, 400),
    },
    "detail_square": {
        aspect_ratio: 1.0,
        width_px: 600, height_px: 600,
        ideal_composition: ["detail"],
        ideal_face_count: (1, 1),
        position_on_spread: (100, 100),
    },
}
```

**Effort:** 1 day

---

### 3.2.3 Photo-to-Slot Matcher

**File:** `core/slot_matcher.py` (NEW)

**Purpose:** Given a set of candidate photos and available slots, assign each photo to the best slot algorithmically.

**This is a **bipartite matching problem**:**
- Left side: candidate photos (with composition metadata)
- Right side: spread slots (with ideal composition specs)
- Edge weights: compatibility score (0–100)
- Goal: Maximize total compatibility across all assignments

**Methods:**

```
match_photos_to_slots(candidate_photos, available_slots):
  1. For each (photo, slot) pair, calculate compatibility_score()
  2. Build compatibility matrix (photo × slot)
  3. Solve bipartite matching (maximize total score)
     - Use Hungarian algorithm or greedy max-weight matching
  4. Return assignments: {slot_id: photo_id, ...}

calculate_compatibility_score(photo_content, slot_type):
  1. composition_match = 0–100 based on ideal_composition alignment
  2. face_count_match = 0–100 based on ideal_face_count range
  3. aspect_ratio_fit = 0–100 based on how well aspect ratios align
  4. variety_bonus = 0–20 bonus if photo type differs from previous 2 slots (prevent repetition)
  5. return weighted_sum(composition_match, face_count_match, aspect_ratio_fit, variety_bonus)

solve_bipartite_matching(compatibility_matrix):
  1. Use scipy.optimize.linear_sum_assignment (Hungarian algorithm)
  2. Returns optimal assignment minimizing total cost
  3. Fallback: greedy max-weight matching if photo/slot counts don't match
```

**Dependencies:**
- `content_analyzer.py` (photo metadata)
- `slot_schema.py` (slot definitions)
- scipy (for Hungarian algorithm)

**Testing:**
- Unit test: High-compatibility photo paired with matching slot ✅
- Unit test: Low-compatibility pairs avoided ✅
- Unit test: Variety constraint prevents all portraits in a row ✅
- Integration test: On sample spread, verify manual reviewer prefers matched result ✅

**Effort:** 2–3 days

---

### 3.2.4 Slot Matching Integration into Album Generation

**File:** `core/album/orchestrator.py` (MODIFY)

**What changes:**
- When generating a spread, instead of sequential fill (photo 1 → slot 1, photo 2 → slot 2):
  1. Get candidate photos for spread (best shots + review set, filtered by event)
  2. Load available slot types for spread template
  3. Run photo-to-slot matcher
  4. Assign photos based on compatibility scores
  5. Apply intelligent crop per slot
  6. Render spread

**Pseudocode:**
```python
def generate_spread(event_photos, spread_template):
    candidate_photos = get_candidate_photos(event_photos)
    available_slots = spread_template.get_slots()
    
    # Analyze candidate photos
    photo_content = [analyze_photo(p) for p in candidate_photos]
    
    # Match to slots
    assignments = match_photos_to_slots(photo_content, available_slots)
    
    # Apply intelligent crop + render
    for slot_id, photo_id in assignments.items():
        photo = candidate_photos[photo_id]
        slot = available_slots[slot_id]
        cropped_photo = apply_intelligent_crop(photo, slot.type)
        render_photo_to_slot(cropped_photo, slot)
    
    return spread_psd
```

**Effort:** 1–2 days

---

## Workstream 3.3: Face Cutout Masks & Artistic Frames

### 3.3.1 Face Segmentation Engine

**File:** `core/face_segmenter.py` (NEW)

**Purpose:** Extract face + shoulders as a segmentation mask (no distortion, no cutting).

**What it does:**
- Use InsightFace face landmarks to identify face region
- Add 30–50px buffer for neck + shoulders
- Create binary mask (face region = white, background = black)
- Output as PNG with transparency

**Methods:**

```
segment_face_region(photo_path, face_bbox, landmarks):
  1. Load face_bbox (x, y, w, h) from face detection
  2. Load landmarks (68 or 106 points around face/head)
  3. Create convex hull around landmarks + buffer zone
  4. Expand convex hull by 40px (shoulders context)
  5. Create binary mask with smooth feathering at edges (5px feather)
  6. Return mask image (numpy array, 0–255)

apply_mask_to_image(photo, mask):
  1. Load photo as RGB
  2. Create RGBA image from photo
  3. Set alpha channel to mask values
  4. Return image with transparency
  
feather_mask_edges(mask, feather_radius=5):
  1. Apply Gaussian blur to mask edges (smooth transition from opaque to transparent)
  2. Return feathered mask (smoother cutout edge, less jagged)
```

**Dependencies:**
- InsightFace landmarks (already available from face detection)
- PIL/OpenCV for mask creation
- scipy.ndimage for Gaussian blur

**Testing:**
- Unit test: Mask fully contains detected face ✅
- Unit test: Shoulders/neck included in mask ✅
- Unit test: Feathering produces smooth edge ✅
- Visual test: Manual review of 10 cutouts from sample wedding ✅

**Effort:** 2–3 days

---

### 3.3.2 Artistic Frame Engine

**File:** `core/frame_renderer.py` (NEW)

**Purpose:** Render various frame styles around a cutout mask (circles, ovals, diamonds, etc.)

**Frame styles (Phase 3):**
- `circle` — simple circular frame
- `oval` — elongated oval (good for portrait photos)
- `diamond` — rotated square (90° angle)
- `rounded_rectangle` — soft corners
- `feathered` — soft gradient edge (no hard frame, just feathering)

**Phase 4 additions:**
- `ornate_gold` — decorative gold border (ornamental flourishes)
- `watercolor` — soft watercolor edge effect
- `brush_stroke` — artistic brush-painted edge

**Methods:**

```
render_frame(cutout_image, frame_style, border_color, border_width):
  1. Create base canvas (transparent PNG)
  2. Based on frame_style:
     a) "circle": draw circle mask, apply to cutout
     b) "oval": draw ellipse mask (aspect ratio from photo orientation)
     c) "diamond": rotate canvas 45°, apply square mask, rotate back
     d) "rounded_rectangle": create path with rounded corners
     e) "feathered": no frame, just alpha feathering (already done in segmenter)
  3. If border_color specified, add stroke around frame edge
  4. If border_width > 0, add outer border (e.g., gold, white, black outline)
  5. Return PNG with transparency + frame
  
  Example: render_frame(bride_cutout, "circle", "white", 5px)
    → circular cutout with 5px white border
```

**Dependencies:**
- PIL (for drawing shapes, borders)
- Numpy (for mask manipulation)

**Testing:**
- Unit test: Circle frame has expected diameter ✅
- Unit test: Oval frame has correct aspect ratio ✅
- Unit test: Border rendered at correct width ✅
- Visual test: 5 different frame styles look good on sample photo ✅

**Effort:** 2–3 days

---

### 3.3.3 Cutout Integration into Spread Rendering

**File:** `core/album/raster.py` or `core/album/psd_builder.py` (MODIFY)

**What changes:**
- When rendering a photo slot that specifies `use_cutout: true`:
  1. Segment face from photo (get mask + cutout PNG)
  2. Render artistic frame around cutout
  3. Place cutout PNG on spread background (not as rectangular crop)
  4. Render background behind cutout (solid color or pattern)

**New spread template parameter:**
```python
slot: {
    name: "portrait_large",
    use_cutout: true,
    frame_style: "circle",
    frame_border: {color: "white", width: 5},
    background_color: "#F5D547",  # sampled from event dominant color
}
```

**Effort:** 1–2 days

---

## Workstream 3.4: Template & Config Updates

### 3.4.1 Template Schema Extension

**File:** `core/album/template_schema.py` (NEW or MODIFY)

**Current template structure:**
```python
Template = {
    name: "basic_2_page",
    spreads: [
        {photo_count: 3, layout: "grid"}
    ]
}
```

**New extended structure:**
```python
Template = {
    name: "editorial_bride_section",
    description: "Bride prep → details → portraits, with cutouts",
    spreads: [
        {
            name: "bride_prep",
            slots: [
                {
                    slot_id: 1,
                    type: "portrait_large",
                    position: (600, 300),
                    size: (1200, 1600),
                    use_cutout: true,
                    frame_style: "circle",
                    frame_border: {color: "white", width: 5},
                    background_color: "auto",  # sample from photo dominant color
                },
                {
                    slot_id: 2,
                    type: "detail_square",
                    position: (300, 300),
                    size: (600, 600),
                    use_cutout: false,  # rectangular crop for detail (henna, etc.)
                    frame_style: "rounded_rectangle",
                },
                ...
            ]
        }
    ],
    color_theme: "haldi",  # determines background colors, text colors
    text_overlays: [
        {text: "The Bride", position: (100, 2800), style: "heading"}
    ]
}
```

**Effort:** 1 day

---

### 3.4.2 Configuration & Default Settings

**File:** `data/default_config.yaml` (MODIFY)

**New settings:**
```yaml
album:
  layout:
    enable_intelligent_matching: true
    enable_face_aware_cropping: true
    enable_cutout_masks: false  # Phase 3 ships with option disabled by default
    cutout_frame_style: "circle"  # options: circle, oval, diamond, rounded_rectangle, feathered
    cutout_border_color: "white"
    cutout_border_width: 5
    
  templates:
    default_template: "editorial_bride_haldi"  # changed from "basic_grid"
    available_templates:
      - editorial_bride_haldi
      - editorial_group_landscape
      - editorial_detail_closeups
    # Phase 4: will add themed template collections
```

**Effort:** 1 day

---

## Workstream 3.5: UI Updates for Manual Override

### 3.5.1 Album Preview Panel (Desktop UI)

**File:** `ui_qt/views/album_preview.py` (MODIFY or NEW)

**What it shows:**
- Live preview of generated spreads before export
- For each spread, show:
  - Current layout (photos in slots)
  - Compatibility score for each photo-slot assignment
  - Option to manually reorder photos (drag-drop between slots)
  - Option to toggle cutout on/off for specific slot

**New controls:**
```
[Spread 1: "The Bride"] 
  ├─ Slot 1 (Portrait Large): bride_001.jpg [compatibility: 98%]
  │   └─ [Edit] [Swap with slot 2] [Toggle Cutout]
  ├─ Slot 2 (Detail Square): bride_detail_henna.jpg [compatibility: 95%]
  │   └─ [Edit] [Swap with slot 1] [Toggle Cutout]
  └─ [Preview] [Save as PSD] [Export as PNG]
```

**Effort:** 2–3 days

---

## Workstream 3.6: Testing & Validation

### 3.6.1 Unit Tests

**New test files:**
- `tests/test_crop_engine.py` — crop window calculations, face safety
- `tests/test_content_analyzer.py` — photo classification accuracy
- `tests/test_slot_matcher.py` — bipartite matching correctness
- `tests/test_face_segmenter.py` — mask generation, feathering
- `tests/test_frame_renderer.py` — frame style rendering

**Test data:**
- Sample photos (portrait, group, detail, landscape)
- Known face bboxes / landmarks for reproducibility
- Expected crop windows, compositions, matches

**Effort:** 2–3 days

---

### 3.6.2 Integration Tests

**Test scenarios:**
1. **End-to-end album generation:**
   - Load sample 200-image wedding
   - Run full pipeline (scan → dedup → blur → face → crop → quality → composition)
   - Generate 8-spread album with intelligent matching + cutout masks
   - Verify: no faces cut, composition matches are sensible, cutout renders correctly

2. **Manual override test:**
   - User manually reorders photos in preview panel
   - Regenerate spread with new order
   - Verify reorder is reflected in final output

3. **Fallback test:**
   - Disable intelligent matching (use sequential fill)
   - Compare output quality (should be obviously worse)
   - Verify fallback option works

**Effort:** 2 days

---

### 3.6.3 Human Validation

**Process:**
1. Generate 5 test albums (different wedding styles: Indian, Western, intimate, large, etc.)
2. Show layouts to 3–5 professional photographers
3. Get feedback on:
   - Does intelligent matching look better than grid?
   - Are any faces cut off? (should be zero)
   - Do cutout masks look good?
   - Any specific slot/frame improvements needed?
4. Iterate based on feedback

**Effort:** 3–4 days (including feedback incorporation)

---

## Phase 3 Summary

### Deliverables
✅ Face-aware intelligent cropping (zero face cutting)
✅ Subject-aware photo-to-slot matching
✅ Face cutout masks with artistic frames
✅ Extended template schema
✅ UI preview + manual override capability
✅ Comprehensive tests + human validation

### Success Criteria
- ✅ Zero faces cut in automated layouts (measured on 10 test albums)
- ✅ Compatibility matching improves layout quality by >30% (photographer preference test)
- ✅ Cutout masks render without artifacts on all frame styles
- ✅ Manual override allows photographer to adjust any photo-slot assignment
- ✅ All tests pass (unit + integration)

### Effort Estimate
**8–10 weeks** (assuming 1 full-time developer)

**Week-by-week breakdown:**
- Week 1–2: Crop engine + content analyzer (Workstreams 3.1–3.2)
- Week 2–3: Slot matcher + bipartite matching (Workstream 3.2)
- Week 3–4: Face segmentation + frame renderer (Workstream 3.3)
- Week 4–5: Template schema + config updates (Workstream 3.4)
- Week 5–6: UI preview panel + manual overrides (Workstream 3.5)
- Week 6–8: Testing, human validation, bug fixes (Workstream 3.6)
- Week 8–10: Polish, documentation, performance optimization

---

## Phase 4: Variable Aspect Ratios & Advanced Features (8–12 weeks)

### Phase 4 Goal
*Ship templated albums that rival hand-designed layouts, with variable aspect ratios, AI-suggested placement, and professional decorative assets.*

---

## Workstream 4.1: Variable Aspect Ratio Layouts

### 4.1.1 Flexible Slot System

**Problem (Phase 3):**
- Each spread has fixed slots (1 portrait, 1 square, 1 landscape)
- Portrait photo forced into landscape slot = bad framing

**Solution (Phase 4):**
- Each spread template defines "slot pool" (flexible slots)
- Algorithm selects which slot types to use based on candidate photos
- Spreads can have 2 portrait slots, or 1 large landscape + 2 squares, etc.

**Implementation:**

**File:** `core/album/flexible_template.py` (NEW)

**New template structure:**
```python
FlexibleTemplate = {
    name: "adaptive_bride_section",
    spreads: [
        {
            name: "bride_journey",
            slot_pool: [
                # Can use any 3 of these slots per spread
                {type: "portrait_large", count_available: 2},
                {type: "portrait_small", count_available: 3},
                {type: "landscape_wide", count_available: 2},
                {type: "detail_square", count_available: 3},
            ],
            slots_to_fill: 4,  # pick best 4 from pool
            layout_rules: [
                "cannot_have_2_portraits_side_by_side",  # too monotonous
                "must_alternate_orientations",  # visual rhythm
            ]
        }
    ]
}
```

**Algorithm:**

```
select_flexible_slot_layout(candidate_photos, slot_pool, slots_to_fill, layout_rules):
  1. Get PhotoContent metadata for all candidates
  2. Group candidates by composition type (portrait, group, detail, landscape)
  3. For each valid combination of slots_to_fill from slot_pool:
     a) Check layout_rules (no 2 portraits adjacent, etc.)
     b) Calculate total compatibility if this slot set is used
  4. Pick slot set that maximizes total compatibility + follows rules
  5. Return selected_slots: [slot1_type, slot2_type, slot3_type, slot4_type]

match_photos_to_flexible_slots(candidate_photos, selected_slots):
  1. Run standard bipartite matching (photo to selected slot types)
  2. Return assignments
  3. No longer locked to 3 photos per spread; adapts to photo content
```

**Dependencies:**
- Photocontent metadata from Phase 3
- Existing slot matcher (reuse/adapt)

**Effort:** 3–4 days

---

### 4.1.2 Layout Rules Engine

**File:** `core/album/layout_rules.py` (NEW)

**Pre-built layout rules:**
```python
LAYOUT_RULES = {
    "no_repetition": lambda slots: not has_consecutive_same_type(slots),
    "vary_orientations": lambda slots: has_alternating_orientations(slots),
    "detail_in_corners": lambda slots: detail_slots not in [slots[0], slots[-1]],
    "wide_in_center": lambda slots: landscape_slots in middle_positions(slots),
}
```

**Methods:**
```
validate_layout_against_rules(slot_layout, rules):
  1. For each rule in rules list:
     - Check if rule(slot_layout) returns True
  2. Return True only if all rules pass

suggest_layout_permutations(slot_pool, slots_to_fill, layout_rules):
  1. Generate all C(n, k) combinations of slots_to_fill from slot_pool
  2. Filter combinations that pass layout_rules
  3. Return valid permutations sorted by viability
```

**Effort:** 1–2 days

---

### 4.1.3 Spread Layout Computation

**File:** `core/album/spread_layout_calculator.py` (NEW)

**Problem:** With variable slot counts and types, where do we position each slot on the 5400×3600 canvas?

**Solution:** Pre-computed layout positions for common patterns.

```python
LAYOUT_PATTERNS = {
    # Pattern: 4 slots (1 large portrait + 3 detail squares)
    (portrait_large, detail_square, detail_square, detail_square): {
        positions: [
            {x: 0, y: 0, w: 2700, h: 3600},   # left side, full height
            {x: 2700, y: 0, w: 1350, h: 1200},   # top-right
            {x: 2700, y: 1200, w: 1350, h: 1200},  # mid-right
            {x: 2700, y: 2400, w: 1350, h: 1200},  # bottom-right
        ],
        visual_balance: "portrait_dominant",
    },
    # Pattern: 2 slots (2 landscape wide)
    (landscape_wide, landscape_wide): {
        positions: [
            {x: 0, y: 0, w: 5400, h: 1800},   # top, full width
            {x: 0, y: 1800, w: 5400, h: 1800},  # bottom, full width
        ],
        visual_balance: "symmetric",
    },
    # ... more patterns
}
```

**Methods:**
```
get_layout_positions(slot_types):
  1. Generate key from slot_types tuple
  2. Look up in LAYOUT_PATTERNS
  3. If exact match found, return positions
  4. If no exact match, compute best-fit layout dynamically

compute_dynamic_layout(slot_types, canvas_w=5400, canvas_h=3600):
  1. Assign slots to grid positions (3×2 grid of zones)
  2. Large slots (portrait_large, landscape_wide) span multiple zones
  3. Small slots (detail_square) fill remaining space
  4. Ensure balanced visual weight across spread
  5. Return positions list
```

**Effort:** 2–3 days

---

## Workstream 4.2: AI-Driven Smart Placement

### 4.2.1 GPT-4V Photo Placement Advisor

**File:** `core/placement_advisor.py` (NEW)

**Purpose:** For each spread, ask GPT-4V which photo should go in which slot based on content + composition.

**Workflow:**
```
1. Get 4–5 candidate photos for spread
2. Get available slot types (from 4.1 flexible layout)
3. Create prompt for GPT-4V:
   "I'm designing a wedding album spread. I have 4 photos and 4 slots.
    Slot 1 (portrait large): ideal for close-ups, main subjects
    Slot 2 (detail square): ideal for close-ups of jewelry, henna, flowers
    Slot 3 (detail square): ideal for candids, side profiles
    Slot 4 (landscape wide): ideal for group shots
    
    Given these 4 photos [IMAGES ATTACHED], suggest optimal placement.
    Consider: composition, emotion, visual flow, variety."
4. Parse GPT-4V response
5. Return placement suggestions with confidence scores
6. Show to photographer; photographer approves before export
```

**Methods:**
```
get_placement_suggestion(candidate_photos, slot_types, spread_context):
  1. Create GPT-4V vision prompt with candidate photo URLs/base64
  2. Include slot descriptions + position on spread
  3. Call OpenAI API (gpt-4-vision, costs $0.01–$0.03 per image)
  4. Parse response (likely JSON or structured text)
  5. Return suggested_assignments: {slot_id: photo_id, confidence: 0–100}

apply_placement_with_approval(suggestions, photographer_review):
  1. Show photographer GUI with suggestions + preview
  2. Allow manual override (drag-drop reordering)
  3. On approval, render spread with selected assignment
```

**Config:**
```yaml
album:
  advanced:
    use_gpt4v_placement: false  # Phase 4 ships with opt-in flag
    gpt4v_api_key: "${OPENAI_API_KEY}"
    gpt4v_model: "gpt-4-vision"
    cost_per_suggestion: 0.015  # average cost
```

**Dependencies:**
- OpenAI API client library
- Environment variable or config file for API key
- Optional: cost tracking / warning if album exceeds cost threshold

**Effort:** 2–3 days

---

### 4.2.2 Fallback: Learned Placement Rules

**Alternative to GPT-4V (if cost is concern or API unavailable):**

**File:** `core/placement_heuristics.py` (NEW)

**Hardcoded rules learned from manual albums:**
```python
PLACEMENT_HEURISTICS = {
    "lead_with_couple": "If couple (2 faces, centered) exists, place in largest slot",
    "bride_before_groom": "Prioritize bride photos earlier in spread",
    "detail_in_corner": "Detail shots (henna, jewelry) typically in small corner slots",
    "wide_at_end": "Group/landscape shots often at end of spread for visual closure",
}
```

**Methods:**
```
apply_placement_heuristics(candidate_photos, slot_types):
  1. Score each photo against each heuristic
  2. Rank photos by score
  3. Assign top-ranked photos to highest-priority slots
  4. Use existing bipartite matcher for final optimization
  5. Return assignments (deterministic, no API call)
```

**Cost:** $0 (no API)  
**Trade-off:** Less sophisticated than GPT-4V, but covers 80% of cases

**Effort:** 1–2 days

---

## Workstream 4.3: Themed Decorative Assets & Color Theming

### 4.3.1 Event Classification & Color Sampling

**File:** `core/album/event_classifier.py` (MODIFY/NEW)

**Current:** Timeline segmentation creates "Event 1, 2, 3..."

**Enhanced (Phase 4):**
- Classify events into standard Indian wedding categories:
  - **Haldi** → yellow, gold, floral motifs
  - **Mehndi** → green, pink, floral patterns
  - **Baraat** → red, burgundy, festive
  - **Ceremony** → multi-color or traditional colors
  - **Reception** → gold, elegant, sophisticated
  - **Portraits** → neutral, studio-quality

**Methods:**
```
classify_event_type(event_timeline_slice):
  1. Analyze metadata: time of day, location, faces present, clothing colors
  2. Sample dominant colors from photos in event
  3. Classify into: haldi, mehndi, baraat, ceremony, reception, portraits
  4. Return: {event_type, confidence_score, dominant_colors: [#F5D547, #E0B547, ...]}

sample_dominant_color(photo_list):
  1. Load each photo
  2. Resize to 100×100
  3. Use k-means clustering (k=5) on pixel colors
  4. Return top 3 colors by cluster size
  5. Filter out white/black/gray (background noise)
  6. Return dominant colors as hex codes
```

**Effort:** 2 days

---

### 4.3.2 Theme Asset Library

**File:** `data/themes/` (NEW directory structure)

**Asset structure:**
```
data/themes/
├── haldi/
│   ├── metadata.yaml
│   ├── colors.yaml          # primary, secondary, accent colors
│   ├── backgrounds/
│   │   ├── solid_gold.png
│   │   ├── watercolor_yellow.png
│   │   ├── floral_pattern_haldi.png
│   └── decorations/
│       ├── flower_border.png
│       ├── flourish_corner.png
│       ├── divider_floral.png
├── mehndi/
│   ├── metadata.yaml
│   ├── colors.yaml
│   ├── backgrounds/
│   │   ├── solid_green.png
│   │   ├── watercolor_green.png
│   ├── decorations/
│       ├── mehndi_pattern.png
│       ├── leaf_border.png
├── baraat/
├── ceremony/
├── reception/
└── portraits/
```

**metadata.yaml (per theme):**
```yaml
theme: haldi
description: "Indian Haldi ceremony — warm yellows, golds, florals"
primary_color: "#F5D547"
secondary_color: "#E0B547"
accent_color: "#D4AF37"
text_color: "#8B4513"
quote_style: "script_romantic"
decorations:
  - flower_border
  - flourish_corner
  - divider_floral
text_overlays:
  - "The Bride"
  - "Happy Moments"
  - "A Successful Love Story"
```

**Effort:** 2–3 days (asset creation) + 1 day (code integration)

---

### 4.3.3 Asset Rendering into Spreads

**File:** `core/album/psd_builder.py` or `core/album/raster.py` (MODIFY)

**What changes:**
- When rendering a spread for a classified event (e.g., Haldi):
  1. Load theme assets (Haldi theme colors, backgrounds, decorations)
  2. Apply primary color as background fill
  3. Layer decorative elements (flower borders, flourish corners)
  4. Apply text overlays ("The Bride")
  5. Render photos on top with cutout frames
  6. Adjust text color to match theme

**Example spread generation (Haldi event):**
```
1. Create 5400×3600 canvas
2. Fill with haldi.solid_gold.png (yellow background)
3. Layer haldi.decorations.flower_border on edges
4. Layer haldi.decorations.flourish_corner at top-left and bottom-right
5. Render photo cutouts with haldi.primary_color borders
6. Add text "The Bride" in haldi.text_color (brown)
7. Export as PSD + PNG
```

**Effort:** 1–2 days

---

## Workstream 4.4: Cover Designer

### 4.4.1 Cover Template Engine

**File:** `core/album/cover_designer.py` (NEW)

**Purpose:** Auto-generate album covers with couple names, date, event type, theme.

**Cover layout:**
```
┌─────────────────────────────┐
│                             │
│     [Decorative Header]     │
│                             │
│      [Couple's Names]       │
│                             │
│        [Event Date]         │
│                             │
│    [Hero Photo - Couple]    │
│                             │
│   [Tagline: "Our Love..."]  │
│                             │
└─────────────────────────────┘
```

**Methods:**
```
generate_cover(couple_names, wedding_date, hero_photo, event_type, theme):
  1. Create 5400×3600 canvas
  2. Load theme assets (background, decorative elements)
  3. Position couple names in script font (Devanagari + English)
  4. Add wedding date below names
  5. Crop hero photo (couple shot) with circular/oval cutout
  6. Center cutout on cover
  7. Add decorative elements around cutout (flowers, flourishes per theme)
  8. Add tagline at bottom (e.g., "Our Love Story", "Forever Begins Here")
  9. Export as PSD + PNG
```

**Config:**
```yaml
album:
  cover:
    enable_auto_design: true
    couple_name_font: "devanagari_script"
    tagline_library: "romantic_bollywood"  # options: romantic_bollywood, elegant_western
```

**Effort:** 2–3 days

---

## Workstream 4.5: Template Marketplace (Future Phase 4+)

### 4.5.1 Community Template Submission

**Future work (not Phase 4.0, but Phase 4.1+):**
- Allow photographers to submit custom templates
- Community rates / reviews templates
- Popular templates featured in gallery
- Paid premium templates (photographer creates, sells to others)

**Effort:** Deferred to Phase 4.1+

---

## Workstream 4.6: Testing & Validation (Phase 4)

### 4.6.1 Variable Layout Tests

```
tests/test_flexible_layouts.py:
  - Test: 4-slot layout computes positions correctly ✅
  - Test: Layout rules prevent repetition ✅
  - Test: Dynamic layout computation for unseen patterns ✅
```

**Effort:** 1–2 days

---

### 4.6.2 GPT-4V Integration Tests

```
tests/test_placement_advisor.py:
  - Mock GPT-4V responses
  - Test: suggestion parsing works ✅
  - Test: fallback to heuristics if API fails ✅
  - Test: cost tracking accurate ✅
```

**Effort:** 1 day

---

### 4.6.3 Theme Asset Rendering Tests

```
tests/test_theme_assets.py:
  - Test: Theme colors load correctly ✅
  - Test: Decorations render without errors ✅
  - Test: Text color contrasts with background ✅
  - Visual test: 10 spreads per theme look cohesive ✅
```

**Effort:** 2 days

---

### 4.6.4 End-to-End Album Generation

**Test scenario:**
1. Load 400-image wedding shoot (Indian wedding, multiple events)
2. Run full Phase 4 pipeline:
   - Event classification (Haldi, Mehndi, Baraat, Ceremony, Reception)
   - Intelligent photo matching (variable slots)
   - GPT-4V placement suggestions (photographer reviews)
   - Theme asset application (yellow for Haldi, green for Mehndi, etc.)
   - Cover design (couple names, date, hero photo)
3. Generate 16-spread album (covers + events)
4. Manual review: does output rival hand-designed album? (Y/N)

**Effort:** 2–3 days

---

## Phase 4 Summary

### Deliverables
✅ Variable aspect ratio layouts (adaptive slots)
✅ Layout rules engine (avoid repetition, visual flow)
✅ AI-driven smart placement (GPT-4V optional)
✅ Fallback heuristic placement (no API cost)
✅ Event classification (Haldi → yellow, Mehndi → green, etc.)
✅ Themed asset library (backgrounds, decorations per event)
✅ Asset rendering into spreads
✅ Cover designer (couple names + date + hero photo)
✅ Comprehensive tests + validation

### Success Criteria
- ✅ Variable layouts improve variety and visual interest (photographer preference test)
- ✅ GPT-4V placement suggestions are helpful (photographer accepts >70%)
- ✅ Themed assets make album look cohesive and event-specific
- ✅ Cover design automatically generates professional-looking covers
- ✅ All tests pass (unit + integration + manual validation)

### Effort Estimate
**8–12 weeks** (assuming 1 full-time developer)

**Week-by-week breakdown:**
- Week 1–2: Flexible slot system + layout rules (Workstream 4.1)
- Week 2–3: GPT-4V placement advisor + fallback heuristics (Workstream 4.2)
- Week 3–4: Event classification + theme asset library (Workstream 4.3)
- Week 4–5: Asset rendering into spreads (Workstream 4.3)
- Week 5–6: Cover designer (Workstream 4.4)
- Week 6–8: Testing, human validation, bug fixes (Workstream 4.6)
- Week 8–12: Polish, documentation, optimization, community feedback

---

## Integration Points Between Phase 3 & 4

### Phase 3 → Phase 4 Dependencies

| Phase 3 Output | Phase 4 Input | Use |
|---|---|---|
| Face-aware crop engine | Smart placement | Know photo framing options |
| Photo content metadata | Flexible slot selection | Decide which slot types needed |
| Artistic frames + cutouts | Theme asset rendering | Layer cutouts with backgrounds |
| Template schema | Flexible templates | Extend schema with slot pools |
| UI preview + overrides | GPT-4V approval UI | Show placement suggestions for review |

### No Breaking Changes
- Phase 3 ships with `enable_intelligent_matching: true` by default
- Phase 4 adds new features, doesn't break Phase 3 features
- Photographers can disable new features (e.g., `use_gpt4v_placement: false`) and fall back to Phase 3 behavior

---

## Risk Mitigation

### Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Face segmentation fails on complex backgrounds** | Cutouts look bad | Fallback to rectangular crop if segmentation confidence < 80% |
| **GPT-4V API rate limits / costs balloon** | Expensive per album | Implement cost tracking + warning; fallback to heuristics |
| **Flexible layouts create visual imbalance** | Album looks amateurish | Use layout_rules engine + human review before shipping |
| **Event classification wrong (Haldi misclassified as Mehndi)** | Wrong theme colors applied | Use confident classification only; photographer can override in config |
| **Theme assets look generic / cheap** | Album quality suffers | Hire professional designer for asset creation; get feedback from photographers |

---

## Success Metrics

### Phase 3 Success Metrics
- Zero faces cut off in any generated layout (audited on 10 test albums)
- Compatibility score improvements >30% vs. grid baseline (A/B comparison)
- Photographer satisfaction >80% (survey: "Would you choose this layout over manual design?")
- Performance: Album generation <5 min (was 10–15 min in MVP)

### Phase 4 Success Metrics
- Variable layouts increase visual variety >40% (designer assessment)
- GPT-4V suggestions accepted >70% without modification (photographer preference)
- Themed assets make event classification >90% recognizable (photographer feedback)
- Cover designs acceptable without manual editing (photographer preference >85%)
- Album generation time <5 min (including GPT-4V calls)

---

## Deployment & Rollout

### Phase 3 Rollout
1. **Beta (Week 8):** Ship to 5–10 photographers (closed beta)
2. **Feedback (Week 8–9):** Gather usage data + feedback
3. **GA (Week 10):** Public release on main branch

### Phase 4 Rollout
1. **Beta (Week 12):** Ship to 20–30 photographers
2. **Feedback (Week 12–14):** Iterate on theme assets + layout variety
3. **GA (Week 14+):** Public release

---

## Future Phases (Post Phase 4)

### Phase 4.1: Community Templates & Marketplace
- Allow photographers to submit templates
- Community rating/review system
- Paid premium templates

### Phase 5: Photoshop Plugin Integration
- Real-time preview of spreads in Photoshop
- Edit → sync back to PhotoFlow
- Professional designer workflow

### Phase 5: Print Lab Integrations
- Direct submit to WHCC / Blurb from PhotoFlow
- Quote generation (auto-calculate cost per spread)
- Order tracking

### Phase 6: Web/SaaS Version
- Cloud-based photographer + designer collaboration
- Remote design approvals
- Cloud backup of analysis cache

---

## Conclusion

**This roadmap is implementation-ready.** Each workstream has clear:
- ✅ Purpose & deliverables
- ✅ File structure (new files, modified files)
- ✅ Methods/pseudocode
- ✅ Dependencies & integration points
- ✅ Tests & validation approach
- ✅ Effort estimate

**Use this to:**
1. Plan sprints (1–2 workstreams per sprint)
2. Assign tasks to developers
3. Track progress in Jira/Linear
4. Define acceptance criteria per workstream
5. Estimate overall timeline (8–10 weeks Phase 3, 8–12 weeks Phase 4)

**Next step:** Pick a developer, assign Workstream 3.1 (Crop Engine), and start building.

---

*Roadmap finalized. Ready to build.*
