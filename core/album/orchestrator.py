"""
Album Orchestrator for PhotoFlow (Phase 1) — the integration spine.

One entry point turns a folder of wedding photos into a complete album
manifest, reusing the engines that already exist:

    folder
      -> load existing analysis (cache) or run analysis (pipeline, dry run)
      -> load + apply manual overrides (from the prior manifest / caller)
      -> build the candidate pool (usable, non-duplicate)
      -> apply auto-edit recipes (cached)
      -> classify events (chronological segments)
      -> build story sections (identity-free Phase 1)
      -> select layout templates + generate spreads
      -> write album_manifest.json

Everything is written into a single :class:`~core.album.project.AlbumProject`,
the canonical state every stage shares. Re-running is idempotent and preserves
manual overrides (they live in the persisted manifest), and unchanged photos
are not re-analyzed (the :class:`~persistence.analysis_cache.AnalysisCache`).

Identity is deliberately absent in Phase 1; the album degrades gracefully to a
time + quality album (Cover / Highlights / Ceremony / Family / Portraits /
Closing) and the person sheets arrive in a later phase without changing this
spine.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Iterable, Mapping, Optional, Union

from core.album.layout import AlbumSpec
from core.album.layout_select import LayoutSelector
import numpy as np

from core.album.analysis_records import (
    CACHE_FILENAME,
    DEFAULT_ALBUM_DIR,
    records_from_result,
)
from core.album.project import (
    AlbumProject,
    EventRecord,
    PersonClusterRecord,
    PhotoRecord,
)
from core.album.story import StoryBuilder
from core.auto_edit import AutoEditError, AutoEditor
from core.person_cluster import FaceRef, PersonClusterer
from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)
from core.pipeline import PhotoFlowPipeline
from core.scanner import ImageScanner
from core.timeline import build_timeline, segment_events
from persistence.analysis_cache import AnalysisCache
from utils.config import load_config
from utils.logger import get_logger

PathLike = Union[str, Path]

logger = get_logger(__name__)

# ``DEFAULT_ALBUM_DIR`` / ``CACHE_FILENAME`` are defined in
# ``core.album.analysis_records`` (imported above) so the UI analysis process
# can reference the same shared-cache location without importing this module.

# Max cosine distance for re-binding a saved person label to a fresh cluster.
LABEL_MATCH_DISTANCE_MAX = 0.4


class AlbumOrchestratorError(Exception):
    """Raised when album generation cannot complete."""


class AlbumOrchestrator:
    """
    Generates a complete :class:`AlbumProject` from a folder.

    Components are injected for testability and default to config-built ones.
    """

    def __init__(
        self,
        config=None,
        album_spec: Optional[AlbumSpec] = None,
        pipeline: Optional[PhotoFlowPipeline] = None,
        scanner: Optional[ImageScanner] = None,
        auto_editor: Optional[AutoEditor] = None,
        story_builder: Optional[StoryBuilder] = None,
        layout_selector: Optional[LayoutSelector] = None,
        enable_identity: bool = True,
        face_detector=None,
        embedder=None,
        clusterer: Optional[PersonClusterer] = None,
    ) -> None:
        self._config = config if config is not None else load_config()
        self.album_spec = album_spec or AlbumSpec(
            page_width_in=12, page_height_in=12, dpi=300
        )
        self._pipeline = pipeline
        self._scanner = scanner or ImageScanner.from_config(self._config)
        self.auto_editor = auto_editor or AutoEditor()
        self.story_builder = story_builder or StoryBuilder()
        self.layout_selector = layout_selector or LayoutSelector()
        # Identity (Phase 2). Components are built lazily so a missing model
        # never blocks import; identity degrades gracefully to a Phase 1 album.
        self.enable_identity = enable_identity
        self._face_detector = face_detector
        self._embedder = embedder
        self._clusterer = clusterer or PersonClusterer()

    @classmethod
    def from_config(cls, config=None, **kwargs) -> "AlbumOrchestrator":
        return cls(config=config if config is not None else load_config(), **kwargs)

    # ----------------------------------------------------------------- #
    # Public API
    # ----------------------------------------------------------------- #
    def generate(
        self,
        source_folder: PathLike,
        output_dir: Optional[PathLike] = None,
        overrides: Optional[Mapping[str, str]] = None,
        reanalyze: bool = False,
        apply_auto_edit: bool = True,
        render_formats: Optional[Iterable[str]] = None,
    ) -> AlbumProject:
        """
        Build the album and write ``album_manifest.json``.

        Args:
            source_folder: Folder of wedding photos.
            output_dir: Where the manifest + cache live (default
                ``<source>/PhotoFlow_Album``).
            overrides: Manual ``path -> category`` overrides applied on top of
                analysis (merged over any from a prior manifest).
            reanalyze: Force re-analysis, ignoring cached quality.
            apply_auto_edit: Compute/store auto-edit recipes for candidates.
            render_formats: Optional iterable of rendered output formats to also
                emit directly (no Photoshop needed): any of ``"png"``, ``"jpg"``,
                ``"pdf"``, ``"psd"``. Written under ``<out_dir>/renders``.

        Returns:
            The populated, saved :class:`AlbumProject`.
        """
        # Analyze the folder and discover person clusters. This prefix is
        # shared with ``prepare_people`` (the people-first flow labels the
        # clusters before this method lays out the album).
        project, out_dir, cache, candidates = self._prepare(
            source_folder, output_dir, overrides=overrides, reanalyze=reanalyze
        )

        # Step: auto-edit recipes for the candidate pool (cached per file).
        if apply_auto_edit:
            self._apply_auto_edit(candidates, cache)

        # Step: event segmentation (classification/naming arrives in Phase 3).
        project.events = self._build_events([r.source_path for r in candidates])

        # Step: story sections (identity-free).
        project.sections = self.story_builder.build(project)

        # Step: layout selection -> spreads.
        project.spreads = self.layout_selector.select(project, self.album_spec)

        # Step: export.
        project.export.retouch_needed = [
            r.source_path for r in candidates if r.faces_detected
        ]
        manifest_path = project.save(out_dir)

        # Also emit a Photoshop builder script alongside the manifest so the
        # photographer can auto-build layered PSD spreads. Non-fatal on failure.
        try:
            from core.album.photoshop_jsx import export_photoshop_jsx

            export_photoshop_jsx(out_dir, project)
        except Exception as exc:  # noqa: BLE001 - JSX export must not break album gen
            logger.warning("Photoshop JSX export failed: %s", exc)

        # Optionally emit rendered spreads (PNG/JPG/PDF/PSD) so photographers
        # without Photoshop get a usable album. Non-fatal on failure.
        if render_formats:
            try:
                from core.album.raster import export_renders

                renders = export_renders(
                    out_dir / "renders", project, render_formats
                )
                logger.info(
                    "Rendered album in format(s): %s", ", ".join(sorted(renders))
                )
            except Exception as exc:  # noqa: BLE001 - rendering must not break gen
                logger.warning("Album render export failed: %s", exc)

        cache.save()

        logger.info(
            "Album generated: %d photo(s), %d candidate(s), %d section(s), "
            "%d spread(s) -> %s",
            len(project.photos),
            len(candidates),
            len(project.sections),
            len(project.spreads),
            manifest_path,
        )
        return project

    def prepare_people(
        self,
        source_folder: PathLike,
        output_dir: Optional[PathLike] = None,
        overrides: Optional[Mapping[str, str]] = None,
        reanalyze: bool = False,
    ) -> AlbumProject:
        """
        Analyze the folder and discover person clusters, WITHOUT laying out an
        album — the people-first flow's first interactive step.

        Returns an :class:`AlbumProject` populated with the photo inventory and
        person clusters (no events/sections/spreads). The photographer labels
        the clusters on this project; re-saving it (or calling
        :meth:`~core.album.project.AlbumProject.save`) persists those labels to
        the manifest, and a subsequent :meth:`generate` re-binds them by
        centroid — so labelling survives into the built album.

        Analysis, face detection, and embeddings are cached, so this pass and
        the later :meth:`generate` share all the heavy work (nothing is
        recomputed between labelling and building).
        """
        project, out_dir, cache, _candidates = self._prepare(
            source_folder, output_dir, overrides=overrides, reanalyze=reanalyze
        )
        project.save(out_dir)
        cache.save()
        logger.info(
            "People prepared: %d photo(s), %d person cluster(s) -> %s",
            len(project.photos),
            len(project.clusters),
            out_dir,
        )
        return project

    # ----------------------------------------------------------------- #
    # Steps
    # ----------------------------------------------------------------- #
    def _prepare(
        self,
        source_folder: PathLike,
        output_dir: Optional[PathLike],
        overrides: Optional[Mapping[str, str]],
        reanalyze: bool,
    ) -> tuple[AlbumProject, Path, AnalysisCache, list[PhotoRecord]]:
        """
        Shared prefix of :meth:`generate` and :meth:`prepare_people`.

        Scans the folder, builds the (cache-backed) photo inventory, applies
        sticky + caller overrides, and discovers person clusters (re-binding any
        labels from a prior manifest). Returns the project, the resolved output
        directory, the open cache, and the album-candidate pool.
        """
        source = Path(source_folder)
        if not source.exists() or not source.is_dir():
            raise AlbumOrchestratorError(f"Source folder not found: {source}")

        out_dir = Path(output_dir) if output_dir is not None else source / DEFAULT_ALBUM_DIR
        cache = AnalysisCache(out_dir / CACHE_FILENAME)

        paths = [str(p) for p in self._scanner.scan(source)]

        project = AlbumProject.new(
            source_folder=source, album_spec=dataclasses.asdict(self.album_spec)
        )

        # Recover prior manual overrides, then merge caller overrides.
        merged_overrides = self._load_prior_overrides(out_dir)
        if overrides:
            merged_overrides.update(dict(overrides))

        # Analysis (cache or pipeline).
        records = self._analyze(source, paths, cache, reanalyze)
        for rec in records:
            project.add_photo(rec)

        # Apply overrides (sticky; persisted in the manifest).
        self._apply_overrides(project, merged_overrides)
        project.overrides = dict(merged_overrides)

        candidates = project.candidate_pool()

        # Identity (detect -> embed -> cluster). Degrades to no clusters when no
        # model is available. Labels from a prior run are re-bound by centroid
        # so the photographer's labelling survives re-analysis.
        project.clusters = self._run_identity(candidates, cache)
        self._rebind_labels(project, self._load_prior_clusters(out_dir))
        return project, out_dir, cache, candidates

    def _analyze(
        self,
        source: Path,
        paths: list[str],
        cache: AnalysisCache,
        reanalyze: bool,
    ) -> list[PhotoRecord]:
        """Build the photo inventory, reusing cached quality when possible."""
        if not reanalyze and paths and cache.all_valid("quality", paths):
            logger.info("Album analysis: reusing cached quality for %d photo(s).", len(paths))
            return [PhotoRecord.from_dict(cache.get("quality", p)) for p in paths]

        # Passing the cache lets the pipeline persist (and reuse) face
        # detections under the "faces" namespace, so the identity stage below
        # never re-runs detection. Records are built by the shared helper so
        # the UI's "Analyze" pass and this album pass classify identically.
        result = self._get_pipeline().run(source, dry_run=True, cache=cache)
        records = records_from_result(result)
        for rec in records:
            cache.put("quality", rec.source_path, rec.to_dict())
        return records

    @staticmethod
    def _apply_overrides(project: AlbumProject, overrides: Mapping[str, str]) -> None:
        for path, category in overrides.items():
            rec = project.get(path)
            if rec is None:
                continue
            rec.category = category
            rec.is_best_shot = category == FOLDER_BEST_SHOTS
            rec.is_duplicate = category == FOLDER_DUPLICATES

    def _apply_auto_edit(self, candidates: list[PhotoRecord], cache: AnalysisCache) -> None:
        for rec in candidates:
            cached = cache.get("edit", rec.source_path)
            if cached is not None:
                rec.edit_recipe = cached
                continue
            try:
                recipe = self.auto_editor.analyze(rec.source_path).as_dict()
            except AutoEditError as exc:
                logger.warning("Auto-edit failed for '%s': %s", rec.source_path, exc)
                continue
            rec.edit_recipe = recipe
            cache.put("edit", rec.source_path, recipe)

    # ----------------------------------------------------------------- #
    # Identity stage
    # ----------------------------------------------------------------- #
    def _run_identity(
        self, candidates: list[PhotoRecord], cache: AnalysisCache
    ) -> list[PersonClusterRecord]:
        """Detect, embed, and cluster faces across the candidate pool."""
        if not self.enable_identity:
            logger.info(
                "Identity stage: disabled (enable_identity=False). "
                "Album will use time+quality layout only (Phase 1)."
            )
            return []
        detector = self._identity_detector()
        embedder = self._identity_embedder()
        if detector is None or embedder is None:
            missing = []
            if detector is None:
                missing.append("face detector (MediaPipe)")
            if embedder is None:
                missing.append("face embedder (InsightFace + onnxruntime)")
            logger.warning(
                "Identity stage DEGRADED — missing: %s. "
                "Album falls back to time+quality layout (Phase 1). "
                "Install the missing packages and re-run to get person sheets.",
                ", ".join(missing),
            )
            return []

        refs: list[FaceRef] = []
        for rec in candidates:
            path = rec.source_path
            # Reuse the face regions cached during analysis (the "faces"
            # namespace written by the pipeline) instead of re-detecting.
            cached_regions = cache.get("faces", path)
            if cached_regions is not None:
                regions = [tuple(float(v) for v in box) for box in cached_regions]
            else:
                try:
                    regions = list(detector.detect(path).regions)
                except Exception as exc:  # noqa: BLE001 - no model / unreadable -> skip
                    logger.debug("Identity: no faces for '%s': %s", path, exc)
                    continue
                cache.put("faces", path, [list(box) for box in regions])
            if not regions:
                continue
            vectors = self._embeddings_for(path, regions, embedder, cache)
            for index, vec in enumerate(vectors):
                refs.append(FaceRef(image_path=path, face_index=index, vector=vec))

        if not refs:
            logger.info("Identity: no faces embedded; album stays Phase 1.")
            return []

        clusters = self._clusterer.cluster(refs)
        records: list[PersonClusterRecord] = []
        for cluster in clusters:
            photos = sorted(cluster.photo_paths)
            records.append(
                PersonClusterRecord(
                    cluster_id=cluster.cluster_id,
                    photos=photos,
                    size=cluster.size,
                    centroid=[float(x) for x in cluster.centroid],
                    representative=photos[0] if photos else None,
                )
            )
        logger.info("Identity: %d person cluster(s) discovered.", len(records))
        return records

    @staticmethod
    def _embeddings_for(path, regions, embedder, cache) -> list[np.ndarray]:
        """Embeddings for one photo's faces, cached per file."""
        cached = cache.get("embedding", path)
        if cached is not None:
            return [np.asarray(v, dtype=np.float32) for v in cached]
        try:
            embeddings = embedder.embed(path, regions)
        except Exception as exc:  # noqa: BLE001 - missing backend / bad crop
            logger.debug("Identity: embedding failed for '%s': %s", path, exc)
            return []
        vectors = [e.vector for e in embeddings]
        cache.put("embedding", path, [v.tolist() for v in vectors])
        return vectors

    def _identity_detector(self):
        if self._face_detector is not None:
            return self._face_detector
        try:
            from core.face_detector import FaceDetector

            self._face_detector = FaceDetector.from_config(self._config)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Identity: face detector unavailable: %s", exc)
            self._face_detector = None
        return self._face_detector

    def _identity_embedder(self):
        if self._embedder is not None:
            return self._embedder
        try:
            from core.face_embedder import FaceEmbedder
            from core.insightface_backend import build_insightface_backend

            self._embedder = FaceEmbedder.from_config(
                self._config, embed_backend=build_insightface_backend()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Identity: embedder unavailable: %s", exc)
            self._embedder = None
        return self._embedder

    @staticmethod
    def _load_prior_clusters(out_dir: Path) -> list[PersonClusterRecord]:
        """Labelled clusters from a previously written manifest, if any."""
        manifest = out_dir / "album_manifest.json"
        if not manifest.is_file():
            return []
        try:
            return [c for c in AlbumProject.load(manifest).clusters if c.label]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read prior clusters: %s", exc)
            return []

    def _rebind_labels(
        self, project: AlbumProject, prior_clusters: list[PersonClusterRecord]
    ) -> None:
        """Re-apply saved labels to fresh clusters by nearest centroid."""
        if not prior_clusters or not project.clusters:
            return
        taken: set[int] = set()
        for prior in prior_clusters:
            target = self._unit(np.asarray(prior.centroid, dtype=np.float32))
            best_id: Optional[int] = None
            best_distance = LABEL_MATCH_DISTANCE_MAX
            for cluster in project.clusters:
                if cluster.cluster_id in taken or not cluster.centroid:
                    continue
                distance = float(
                    1.0 - np.dot(target, self._unit(np.asarray(cluster.centroid, np.float32)))
                )
                if distance <= best_distance:
                    best_id = cluster.cluster_id
                    best_distance = distance
            if best_id is not None:
                project.label_cluster(best_id, prior.label, prior.side)
                taken.add(best_id)

    @staticmethod
    def _unit(vec: np.ndarray) -> np.ndarray:
        vec = np.asarray(vec, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        return vec if norm == 0.0 else vec / norm

    @staticmethod
    def _build_events(paths: list[str]) -> list[EventRecord]:
        if not paths:
            return []
        timeline = build_timeline(paths)
        events: list[EventRecord] = []
        for seg in segment_events(timeline):
            events.append(
                EventRecord(
                    index=seg.index,
                    name=f"Event {seg.index + 1}",  # named/classified in Phase 3
                    photos=list(seg.photos),
                    start=seg.start.isoformat(),
                    end=seg.end.isoformat(),
                )
            )
        return events

    @staticmethod
    def _load_prior_overrides(out_dir: Path) -> dict[str, str]:
        """Recover sticky overrides from a previously written manifest, if any."""
        manifest = out_dir / "album_manifest.json"
        if not manifest.is_file():
            return {}
        try:
            return dict(AlbumProject.load(manifest).overrides)
        except Exception as exc:  # noqa: BLE001 - a bad prior manifest must not block
            logger.warning("Could not read prior overrides from '%s': %s", manifest, exc)
            return {}

    def _get_pipeline(self) -> PhotoFlowPipeline:
        if self._pipeline is None:
            pipeline = PhotoFlowPipeline.from_config(self._config)
            # Share ONE face detector between the pipeline's quality stage and
            # the identity stage. The pipeline writes its detections to the
            # "faces" cache, which the identity stage then reuses — so faces are
            # detected exactly once per photo instead of twice. Only applied to
            # a pipeline we build ourselves (an injected pipeline is left as-is).
            detector = self._identity_detector()
            if detector is not None:
                pipeline.face_detector = detector
            self._pipeline = pipeline
        return self._pipeline
