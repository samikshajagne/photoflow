# PhotoFlow — Person-Aware Albums (InsightFace) Setup & Run Guide

This guide gets you from a folder of wedding photos to a **person-aware album**
(Bride / Groom / Couple / Family sheets) using a free, fully-offline
face-recognition model. No cloud, no paid services.

## 1. Install the recognition backend

```bash
pip install insightface onnxruntime
```

- `insightface` provides the `buffalo_l` model pack (SCRFD detector + ArcFace
  `w600k_r50` recognition).
- `onnxruntime` is the CPU inference engine (use `onnxruntime-gpu` instead only
  if you have a CUDA GPU).
- **First run downloads `buffalo_l` (~300 MB)** to `~/.insightface/models/`.
  This is a one-time download; after that everything is offline.

Nothing else changes — the model plugs into PhotoFlow's existing
`FaceEmbedder` seam, so detection, clustering, story, layout, and export are
unchanged.

## 2. Generate the album (discovers people)

```python
from core.album.orchestrator import AlbumOrchestrator

orch = AlbumOrchestrator()              # identity is enabled by default
project = orch.generate(r"D:\shoots\smith_wedding")
print(project.export.manifest_path)      # ...\PhotoFlow_Album\album_manifest.json
```

This runs the full pipeline and, because a real backend is installed, the
identity stage now detects faces, embeds them, and clusters them into people.
Until you label them the album is the time/quality (Phase 1) album — labelling
is what unlocks the person sheets.

You can see who was found:

```python
for c in project.clusters_for_review():     # largest (most photographed) first
    print(c.cluster_id, c.size, c.representative)
```

## 3. Label the people

Launch the labelling screen on the album folder:

```bash
python -m ui_qt.identity_app "D:\shoots\smith_wedding\PhotoFlow_Album"
```

For each detected person you get a representative thumbnail, how many photos
they appear in, and pickers for **role** (Bride, Groom, Mother, Father,
Brother, Sister, Relative, Friend) and, for family, a **side** (bride / groom).
A single choice labels *all* of that person's photos. Click **Apply labels** to
save them into the manifest.

(Programmatic equivalent, if you prefer:)

```python
from core.album.project import ROLE_BRIDE, ROLE_GROOM, ROLE_MOTHER, SIDE_BRIDE
project.label_cluster(0, ROLE_BRIDE)
project.label_cluster(1, ROLE_GROOM)
project.label_cluster(2, ROLE_MOTHER, side=SIDE_BRIDE)
project.save(project.export.manifest_path)
```

## 4. Regenerate — now person-aware

```python
project = AlbumOrchestrator().generate(r"D:\shoots\smith_wedding")
print([s.name for s in project.sections])
# -> Cover, Couple, Bride, Groom, Bride Family, Groom Family, Close Family,
#    Highlights, Ceremony, Closing  (empty sections are omitted)
```

Your labels persist in the manifest and are **re-bound to the freshly computed
clusters by centroid**, so re-running never makes you label again. The story
builder now emits the person sheets and the layout selector lays them out:
Couple → hero spread, Bride/Groom → portrait pairs, Family → grids.

## 5. Tuning

- **Clustering tightness** — if one person is split into several clusters,
  loosen the threshold; if two people merge, tighten it:

  ```python
  from core.person_cluster import PersonClusterer
  orch = AlbumOrchestrator(clusterer=PersonClusterer(distance_max=0.35))
  ```

  Lower = stricter (more, purer clusters); higher = looser. Tune on a real
  shoot; ~0.3–0.45 is a sensible range for ArcFace.

- **Tiny faces** are skipped (`FaceEmbedder(min_face_px=...)`) to avoid noisy
  background-guest embeddings.

## 6. Performance

- The embedding step is the cost: ArcFace on CPU is ~tens of ms per face. A
  900-photo shoot with ~2 faces/photo (~1,800 faces) is roughly a minute or two
  on CPU; a GPU (`onnxruntime-gpu`, `ctx_id>=0`) is much faster.
- **Embeddings are cached** per photo (keyed by size+mtime), so only changed
  photos are re-embedded on a re-run — labelling/regeneration loops are cheap.
- Clustering and story/layout are fast (greedy cosine + pure logic).
- Memory is modest: 512-d float32 per face.

## 7. Troubleshooting

- *No people found / album stays Phase 1*: confirm `insightface` + `onnxruntime`
  import cleanly and the model downloaded to `~/.insightface`. The identity
  stage **degrades gracefully** (no crash) when the model is missing — so a
  Phase 1 album with no person sheets usually means the backend didn't load.
- *Wrong person merged/split*: adjust `distance_max` (section 5).
- *Want to disable identity*: `AlbumOrchestrator(enable_identity=False)`.

## 8. Privacy

Recognition is **local-only**: embeddings are computed on your machine and used
solely to group the photos of *this* shoot. They are not identity lookups
against any external database, and nothing leaves your computer.
