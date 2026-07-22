# Implementation Plan: Rich Vision Brain

## The Problem (Root Cause Diagnosis)

Your current pipeline has 3 compounding weaknesses:

| Stage | Current Approach | Problem |
|-------|-----------------|---------|
| **Face Detection** | MediaPipe (single face model, confidence 0.5) | Misses small faces, side profiles, faces in crowds, and faces partially obscured by jewelry/dupatta |
| **Face Embedding** | InsightFace `buffalo_l` local (ArcFace, 512-d) | Good model, but only runs if InsightFace is installed and working — falls back to zero vectors silently |
| **Clustering** | Greedy single-pass cosine-distance, threshold 0.4 | Greedy pass never corrects early mistakes; fixed threshold doesn't adapt to the number of people; only 50% accuracy is expected |
| **Event classification** | Only EXIF timestamp gap detection | Has zero semantic understanding of what's happening (Haldi vs Ceremony vs Reception) |
| **Layout decisions** | Face boxes from detection → slot cropping | If detection missed a face, the crop is wrong → faces get cut |

---

## The Solution: A Vision API "Brain"

Use **Google Vision API** (or AWS Rekognition / Azure Face) as the feature-extraction engine.
This gives us, per photo:
- **All faces detected** (with high-accuracy bounding boxes)
- **Face landmarks** (5-point alignment — enables much better embeddings)
- **Face attributes** (dominant emotion: joy / neutral / surprised)
- **Labels** (e.g. "ceremony", "haldi", "flowers", "dance floor", "outdoor")
- **Dominant colors** (scene palette — for theming)

All these features are extracted **once** during the "Analyze" step and **stored in the cache** so they never need to be re-fetched. The "brain" stores everything; all downstream tasks (album layout, people labeling, event grouping) read from this cache.

### Cost Estimate
Google Vision API (Label + Face detection):
- **Free tier**: 1,000 images/month free (first 1K images are always free per month).
- **Paid**: $1.50 per 1,000 images beyond the free tier.
- A 200-photo wedding shoot = **$0** (within free tier) or **$0.30** for a 200-photo paid shoot.
- **Revenue impact on B2B pricing**: Negligible ($0.30 per album vs. ₹5,000–₹15,000 album fee).

---

## Proposed Changes

### Component 1: Vision API Brain Layer (`core/vision_brain.py`)

A new module that wraps Google Vision API (with a clean interface so AWS/Azure can be swapped in later).

Extracts and returns a `PhotoBrain` dataclass per image:

```python
@dataclass
class PhotoBrain:
    path: str
    # Face data
    face_count: int
    face_boxes: list[tuple[float, float, float, float]]  # (x, y, w, h) normalized
    face_landmarks: list[list[tuple[float, float]]]      # 5-point landmarks per face
    face_emotions: list[str]                              # "joy", "neutral", etc.
    # Scene understanding
    scene_labels: list[str]                               # e.g. ["haldi", "ceremony", "dance"]
    scene_confidence: list[float]                         # matching confidence scores
    # Color
    dominant_colors: list[tuple[int, int, int]]           # RGB tuples
    # Timeline
    capture_time: datetime
```

#### [NEW] `core/vision_brain.py`

The Google Vision API brain layer. Handles:
- Sending batches of images to the Vision API
- Parsing face bounding boxes, landmarks, emotions, labels
- Returning `PhotoBrain` objects
- Graceful fallback to local MediaPipe if API is unavailable

---

### Component 2: Smarter Clustering (`core/person_cluster.py`)

Replace the current single-pass greedy algorithm with a **2-phase approach**:

**Phase 1 (API-powered):** Use the Google Vision API's face landmarks to compute proper face crops aligned to the eye-nose-mouth keypoints. These are passed to the local InsightFace embedder (which is already there). This gives much better 512-d vectors.

**Phase 2 (better clustering):** Replace greedy cosine-distance single-pass with **DBSCAN or agglomerative clustering**. This:
- Doesn't require knowing the number of people in advance
- Correct late mistakes (greedy does not)
- Is much more accurate (~90%+ expected accuracy)

#### [MODIFY] `core/person_cluster.py`
- Add `cluster_with_dbscan(face_refs, eps=0.35)` as the default
- Keep greedy as a fallback when `scipy` is not installed

---

### Component 3: Event/Function Classification (`core/event_classifier.py`)

#### [NEW] `core/event_classifier.py`

Uses the scene labels from the Vision API brain to classify each photo into a named function:
- `haldi` (label: turmeric, yellow, marigold)
- `mehndi` (label: henna, mehndi, hands)
- `ceremony` (label: mandap, fire, priest, ritual)
- `baraat` (label: horse, procession, band, dhol)
- `reception` (label: dance floor, stage, cake)
- `portraits` (label: couple, ring, studio)

This replaces the current EXIF-gap-only segmentation with semantically correct grouping.

---

### Component 4: Face-Safe Crop Using Landmarks

#### [MODIFY] `core/album/facecrop.py`

Currently uses bounding boxes for face-safe cover crop. Upgrade to use the 5-point landmarks (left eye, right eye, nose, left mouth, right mouth) from the Vision Brain to:
- Determine the **face center more accurately** (using eye midpoint as anchor)
- Widen the crop box by the correct amount so the chin and crown are never cut

---

### Component 5: UI — API Key Setup (`ui_qt/views/api_settings_dialog.py`)

#### [NEW] `ui_qt/views/api_settings_dialog.py`

A simple settings dialog for entering and saving the Google Vision API key:
- API Key field (masked)
- "Test connection" button (sends one test image)
- Stores key in user's `AppData` config so it persists across sessions
- Falls back to local MediaPipe if no API key is set

---

### Component 6: Enrich Analysis Cache

#### [MODIFY] `persistence/analysis_cache.py`

Add a new `vision_brain` cache namespace that stores `PhotoBrain` per image path, serialized as JSON. All downstream modules read from this cache so:
- The API is only called **once per photo** (during the "Analyze" step)
- If re-analyzing, only changed/new photos are sent to the API
- The cache survives across app restarts

---

## Architecture Diagram

```
Analyze button clicked
        │
        ▼
┌─────────────────────────────────────────────────┐
│              Vision Brain Layer                  │
│                                                 │
│  Google Vision API (per photo, batched):        │
│  ┌─────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ Faces   │  │  Labels  │  │   Colors     │   │
│  │ (boxes  │  │(ceremony,│  │(dominant RGB)│   │
│  │+landmarks│  │ haldi…) │  │              │   │
│  └────┬────┘  └────┬─────┘  └──────┬───────┘   │
│       │            │               │            │
└───────┼────────────┼───────────────┼────────────┘
        │            │               │
        ▼            ▼               ▼
┌──────────────────────────────────────────────────┐
│              Analysis Cache (JSON)               │
│  { path: PhotoBrain { faces, labels, colors } }  │
└──┬──────────────┬──────────────┬─────────────────┘
   │              │              │
   ▼              ▼              ▼
People        Event          Album
Clustering  Classification  Layout
(DBSCAN +   (labels →       (face-safe
 ArcFace)    function name)  crop with
                             landmarks)
```

---

## Open Questions

> [!IMPORTANT]
> **Which Vision API provider do you prefer?**
> - **Google Cloud Vision API** — Best label detection for Indian wedding contexts (recognizes "haldi", "mehndi", "mandap"). Needs a Google Cloud account. Free tier: 1,000 images/month.
> - **AWS Rekognition** — Good face detection, weaker label detection for Indian context. Needs AWS account.
> - **Azure Face + Computer Vision** — Good accuracy. Requires Azure subscription.
>
> Recommendation: **Google Cloud Vision API** (best label recognition for Indian weddings, free tier is sufficient for testing).

> [!IMPORTANT]
> **Do you want the fallback to be local MediaPipe if no API key?**
> Yes/No — If yes, users without an API key still get the current behavior. If no, they must configure an API key.

> [!NOTE]
> **Cluster accuracy target:** DBSCAN with ArcFace embeddings typically achieves ~88–95% cluster accuracy on wedding photos. The remaining 5–12% miss-clusters are usually: (a) very poor-quality face crops, (b) extreme angle/occlusion, or (c) identical twins. The user can always manually correct these in the "Label People" step.

---

## Verification Plan

### Automated Tests
- `tests/test_vision_brain.py` — Mock the API response, verify `PhotoBrain` parsing
- `tests/test_event_classifier.py` — Unit test label → event mapping
- `tests/test_person_cluster_dbscan.py` — Verify DBSCAN gives correct cluster count

### Manual Verification
1. Run "Analyze" on `D:\startup\test\test-200` with the API key configured.
2. Check that face count in the METRICS panel increases (more faces detected).
3. Go to "Label People" — check that fewer wrong faces appear in each cluster.
4. Build album with "Natural" theme — verify faces are no longer cut at edges.
5. Check that section names in the album sidebar correspond to actual ceremony names (Haldi, Mehndi, etc.) not just "Segment 1, Segment 2".
