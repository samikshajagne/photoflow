"""
End-to-end orchestration for PhotoFlow (Milestone 2 capstone).

This module ties the individual stages together into a single runnable
pipeline:

    scan  ->  duplicate detection  ->  blur detection  ->  face detection
          ->  quality scoring  ->  organize

:class:`PhotoFlowPipeline` wires the scanner, duplicate detector, blur
detector, face detector, quality scorer, and organizer. Components are
injected (with a :meth:`~PhotoFlowPipeline.from_config` convenience builder)
so the pipeline is easy to test with fakes and so each stage keeps its own
configuration.

Quality scores (which now incorporate blur, brightness, contrast, and face
presence) refine which member of a duplicate group is kept as its
representative -- the highest-quality member wins. Duplicate *detection*
itself is unchanged. A ``dry_run`` mode classifies and scores every photo
and reports the counts **without copying anything**.

Face detection failures are non-fatal: an image that cannot be analyzed for
faces is simply scored as having none, and its path is recorded.

Scope: orchestration only -- no new analysis beyond sequencing the existing
engines and summarizing the outcome.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from core.blur_detector import BlurDetectionError, BlurDetector, BlurResult
from core.duplicate_detector import DuplicateDetector
from core.face_detector import FaceDetectionError, FaceDetector, FaceResult
from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
    OrganizationResult,
    PhotoOrganizer,
)
from core.quality_scorer import QualityResult, QualityScorer, QualityScoringError
from core.scanner import ImageScanner
from utils.logger import get_logger

if TYPE_CHECKING:
    from utils.config import AppConfig

logger = get_logger(__name__)

PathLike = Union[str, Path]

# Category keys reported in counts, in display order.
_REPORT_FOLDERS: tuple[str, ...] = (
    FOLDER_BEST_SHOTS,
    FOLDER_DUPLICATES,
    FOLDER_BLURRY,
    FOLDER_REVIEW,
)

# --- BestShots selection (threshold-driven, not quota-driven) ----------------
# BestShots is drawn from the pool of *usable*, *deduplicated* photos (each
# duplicate group contributes only its best member; unique photos compete on
# equal footing). A photo is a BestShot iff its quality is at or above
# ``BEST_SHOTS_QUALITY_FLOOR`` -- however many that is. There is deliberately
# NO top-percentage and NO min/max cap: the quality score decides, so an
# excellent shoot keeps all its excellent photos and a weak shoot is not padded
# with mediocre ones to hit a quota.
#
# Because the floor is now the *only* gate, the bar is set a little higher (75)
# than when a cap also constrained the set. Intended to be tuned on real
# wedding datasets and made configurable later. A higher, well-tuned quality
# score matters more now that nothing else limits the set.
BEST_SHOTS_QUALITY_FLOOR: float = 75.0


class PipelineError(Exception):
    """Raised when the pipeline cannot complete a run."""


@dataclasses.dataclass(frozen=True)
class PipelineResult:
    """
    Summary of a single pipeline run.

    Attributes:
        input_folder: The scanned source folder.
        scanned_count: Number of image files found.
        duplicate_group_count: Number of duplicate groups detected.
        duplicate_count: Number of images that are duplicates (excludes the
            kept representative of each group).
        blurry_count: Number of images flagged blurry.
        faces_detected_count: Number of images found to contain a face.
        dry_run: Whether this run only previewed (no files copied).
        output_root: The populated PhotoFlow_Output directory, or None for a
            dry run.
        category_counts: Files-per-category (Duplicates/Blurry/Review) that
            were (or would be) written.
        organization: The underlying OrganizationResult, or None for a dry run.
        blur_failures: Paths the blur stage could not analyze (skipped).
        face_failures: Paths the face stage could not analyze (treated as no
            faces).
        quality_results: Per-image QualityResult scores.
        best_shot_candidates: The photos selected as BestShots for the whole
            shoot: every usable, non-duplicate photo whose quality is at or
            above ``BEST_SHOTS_QUALITY_FLOOR`` (no percentage, no cap), ordered
            best-first. These are routed to the BestShots folder and are the
            album source.
    """

    input_folder: str
    scanned_count: int
    duplicate_group_count: int
    duplicate_count: int
    blurry_count: int
    faces_detected_count: int
    dry_run: bool
    output_root: Optional[str]
    category_counts: dict[str, int]
    organization: Optional[OrganizationResult]
    blur_failures: tuple[str, ...]
    face_failures: tuple[str, ...]
    quality_results: tuple[QualityResult, ...]
    best_shot_candidates: tuple[str, ...]


class PhotoFlowPipeline:
    """
    Orchestrates scanning, duplicate/blur/face/quality analysis, and organizing.

    Args:
        scanner: Enumerates the image files to process.
        duplicate_detector: Detects exact/near duplicate groups.
        blur_detector: Scores each image for blur.
        organizer: Copies images into category folders.
        quality_scorer: Scores each image 0-100. Optional; defaults to a
            default-weighted scorer so existing construction keeps working.
        face_detector: Detects faces per image. Optional; defaults to a
            default-configured detector.
    """

    def __init__(
        self,
        scanner: ImageScanner,
        duplicate_detector: DuplicateDetector,
        blur_detector: BlurDetector,
        organizer: PhotoOrganizer,
        quality_scorer: Optional[QualityScorer] = None,
        face_detector: Optional[FaceDetector] = None,
    ) -> None:
        self.scanner = scanner
        self.duplicate_detector = duplicate_detector
        self.blur_detector = blur_detector
        self.organizer = organizer
        # Optional so existing construction keeps working; fall back to
        # default-configured components.
        self.quality_scorer = quality_scorer if quality_scorer is not None else QualityScorer()
        self.face_detector = face_detector if face_detector is not None else FaceDetector()

    @classmethod
    def from_config(cls, config: "AppConfig") -> "PhotoFlowPipeline":
        """Build a pipeline with every stage configured from ``config``."""
        return cls(
            scanner=ImageScanner.from_config(config),
            duplicate_detector=DuplicateDetector.from_config(config),
            blur_detector=BlurDetector.from_config(config),
            organizer=PhotoOrganizer.from_config(config),
            quality_scorer=QualityScorer.from_config(config),
            face_detector=FaceDetector.from_config(config),
        )

    def run(
        self,
        input_folder: PathLike,
        destination_root: Optional[PathLike] = None,
        dry_run: bool = False,
    ) -> PipelineResult:
        """
        Run the full pipeline over ``input_folder``.

        Args:
            input_folder: Folder of photos to analyze and organize.
            destination_root: Where the PhotoFlow_Output folder is created.
                Defaults to ``input_folder`` itself. Ignored when ``dry_run``
                is true.
            dry_run: If true, classify, score, and count only; copy nothing.

        Returns:
            A :class:`PipelineResult` summarizing the run.

        Raises:
            PipelineError: if scanning, detection, or organization fails in a
                way that prevents completion.
        """
        source = Path(input_folder)
        logger.info("Pipeline starting on '%s' (dry_run=%s).", source, dry_run)

        try:
            images = self.scanner.scan(source)
            duplicate_results = self.duplicate_detector.detect(source)
        except Exception as exc:  # scanner/detector raise their own error types
            raise PipelineError(f"Pipeline failed during analysis: {exc}") from exc

        blur_results, blur_failures = self._run_blur(images)
        face_by_path, face_failures = self._run_faces(images)

        quality_results, quality_by_path, usable_by_path = self._run_quality(
            blur_results, face_by_path
        )

        # Re-pick each duplicate group's representative as its highest-quality
        # member. Duplicate *detection* is untouched (duplicate_detector still
        # decides membership); only the choice of which member to keep is
        # refined here using quality (subject sharpness + exposure + faces).
        ranked_results = self._rerank_representatives(duplicate_results, quality_by_path)
        duplicate_paths = self._duplicate_paths(ranked_results)

        # BestShots is the top of the WHOLE shoot, not just duplicate keepers:
        # rank the usable, deduplicated pool by quality and select the top slice.
        best_shot_candidates = self._select_best_shots(
            images, quality_by_path, usable_by_path, duplicate_paths
        )

        # The Blurry bin now reflects the conservative usability gate (clearly
        # unusable photos only), not the quality ranking. Synthesize the
        # blur-result list the organizer consumes from that verdict so its
        # precedence (BestShots > Duplicates > Blurry > Review) is preserved:
        # a duplicate that is also unusable still lands in Duplicates.
        usability_results = self._usability_blur_results(quality_results)

        duplicate_count = len(duplicate_paths)
        faces_detected_count = sum(1 for r in face_by_path.values() if r.faces_detected)

        if dry_run:
            plan = self.organizer.plan(
                original_paths=images,
                duplicate_results=ranked_results,
                blur_results=usability_results,
                best_shots=best_shot_candidates,
            )
            counts = self._counts_from_categories(category for _, category in plan)
            organization: Optional[OrganizationResult] = None
            output_root: Optional[str] = None
        else:
            dest = Path(destination_root) if destination_root is not None else source
            try:
                organization = self.organizer.organize(
                    original_paths=images,
                    duplicate_results=ranked_results,
                    blur_results=usability_results,
                    destination_root=dest,
                    best_shots=best_shot_candidates,
                )
            except Exception as exc:
                raise PipelineError(f"Pipeline failed during organization: {exc}") from exc
            counts = organization.category_counts()
            output_root = organization.output_root

        blurry_count = counts[FOLDER_BLURRY]

        result = PipelineResult(
            input_folder=str(source),
            scanned_count=len(images),
            duplicate_group_count=len(ranked_results["groups"]),
            duplicate_count=duplicate_count,
            blurry_count=blurry_count,
            faces_detected_count=faces_detected_count,
            dry_run=dry_run,
            output_root=output_root,
            category_counts=counts,
            organization=organization,
            blur_failures=tuple(blur_failures),
            face_failures=tuple(face_failures),
            quality_results=tuple(quality_results),
            best_shot_candidates=best_shot_candidates,
        )
        logger.info(
            "Pipeline finished: scanned=%d duplicates=%d blurry=%d faces=%d -> %s",
            result.scanned_count,
            result.duplicate_count,
            result.blurry_count,
            result.faces_detected_count,
            result.category_counts,
        )
        return result

    def _run_blur(self, images: list[Path]) -> tuple[list[BlurResult], list[str]]:
        """Score each image; collect failures instead of aborting the run."""
        results: list[BlurResult] = []
        failures: list[str] = []
        for path in images:
            try:
                results.append(self.blur_detector.detect(path))
            except BlurDetectionError as exc:
                logger.warning("Blur analysis failed for '%s': %s", path, exc)
                failures.append(str(path))
        return results, failures

    def _run_faces(
        self, images: list[Path]
    ) -> tuple[dict[str, FaceResult], list[str]]:
        """
        Detect faces per image, keyed by normalized path.

        Failures are non-fatal: a failed image is omitted from the map (so it
        is treated as having no faces downstream) and its path is recorded.
        """
        by_path: dict[str, FaceResult] = {}
        failures: list[str] = []
        for path in images:
            try:
                result = self.face_detector.detect(path)
            except FaceDetectionError as exc:
                logger.warning("Face analysis failed for '%s': %s", path, exc)
                failures.append(str(path))
                continue
            by_path[self._normalize(result.image_path)] = result
        return by_path, failures

    def _run_quality(
        self,
        blur_results: list[BlurResult],
        face_by_path: dict[str, FaceResult],
    ) -> tuple[list[QualityResult], dict[str, float], dict[str, bool]]:
        """
        Quality-score every successfully blur-analyzed image.

        Reuses each image's blur score and face result (no re-analysis),
        passing face regions through so sharpness is measured on the subject.
        Returns the results plus two normalized-path lookups: one of quality
        scores (for representative re-ranking and BestShots selection) and one
        of usability verdicts (for routing the Blurry bin).
        """
        results: list[QualityResult] = []
        by_path: dict[str, float] = {}
        usable_by_path: dict[str, bool] = {}
        for blur in blur_results:
            key = self._normalize(blur.path)
            face = face_by_path.get(key)
            faces_detected = bool(face.faces_detected) if face is not None else False
            face_count = int(face.face_count) if face is not None else 0
            face_regions = tuple(face.regions) if face is not None else ()
            try:
                quality = self.quality_scorer.score(
                    blur.path,
                    blur.blur_score,
                    faces_detected=faces_detected,
                    face_count=face_count,
                    face_regions=face_regions,
                )
            except QualityScoringError as exc:
                logger.warning("Quality scoring failed for '%s': %s", blur.path, exc)
                continue
            results.append(quality)
            norm = self._normalize(quality.image_path)
            by_path[norm] = quality.quality_score
            usable_by_path[norm] = quality.usable
        return results, by_path, usable_by_path

    def _duplicate_paths(self, ranked_results: dict) -> set[str]:
        """Normalized paths of every non-representative duplicate member."""
        paths: set[str] = set()
        for group in ranked_results["groups"]:
            for dup in group["duplicates"]:
                paths.add(self._normalize(dup))
        return paths

    def _select_best_shots(
        self,
        images: list[Path],
        quality_by_path: dict[str, float],
        usable_by_path: dict[str, bool],
        duplicate_paths: set[str],
    ) -> tuple[str, ...]:
        """
        Select BestShots from the whole shoot, threshold-driven.

        Candidate pool = scanned photos that are usable and are not
        non-representative duplicates (so each duplicate group contributes only
        its kept member, and unique photos are eligible). Every candidate whose
        quality is at or above ``BEST_SHOTS_QUALITY_FLOOR`` is selected -- no
        percentage and no min/max cap. The result is ordered best-first (ties
        broken by path) for deterministic, display-friendly output. Returns
        original path strings.
        """
        selected: list[tuple[float, str]] = []
        for img in images:
            norm = self._normalize(img)
            if norm in duplicate_paths:
                continue
            if not usable_by_path.get(norm, False):
                continue
            score = quality_by_path.get(norm)
            if score is None or score < BEST_SHOTS_QUALITY_FLOOR:
                continue
            selected.append((score, str(img)))

        # Highest quality first; ties broken by path for deterministic output.
        selected.sort(key=lambda item: (-item[0], item[1]))
        return tuple(path for _, path in selected)

    def _usability_blur_results(
        self, quality_results: list[QualityResult]
    ) -> list[BlurResult]:
        """
        Translate usability verdicts into the BlurResult list the organizer
        consumes, where ``is_blurry`` means "clearly unusable". Images without
        a quality result (e.g. decode failures) are intentionally omitted so
        they fall through to Review rather than the Blurry bin.
        """
        return [
            BlurResult(
                path=q.image_path,
                blur_score=q.sharpness,
                is_blurry=not q.usable,
            )
            for q in quality_results
        ]

    def _rerank_representatives(
        self, duplicate_results: dict, quality_by_path: dict[str, float]
    ) -> dict:
        """
        Return a copy of ``duplicate_results`` whose representative for each
        group is its highest-quality member.

        Ties (e.g. exact copies with equal scores) fall back to the
        lexicographically-smallest path, preserving deterministic output.
        Members without a quality score (analysis failed) sort last. The
        original ``duplicate_results`` is not mutated.
        """
        new_groups = []
        for group in duplicate_results["groups"]:
            members = [group["representative"], *group["duplicates"]]
            # Lower sort key wins: highest quality first, then smallest path.
            best = min(
                members,
                key=lambda p: (-quality_by_path.get(self._normalize(p), -1.0), p),
            )
            duplicates = sorted(m for m in members if m != best)
            new_groups.append({"representative": best, "duplicates": duplicates})
        return {"groups": new_groups}

    @staticmethod
    def _normalize(path: PathLike) -> str:
        """Canonicalize a path for reliable cross-stage matching."""
        return str(Path(path).resolve(strict=False))

    @staticmethod
    def _counts_from_categories(categories) -> dict[str, int]:
        """Tally categories, always reporting all active folders."""
        counter = Counter(categories)
        return {folder: counter.get(folder, 0) for folder in _REPORT_FOLDERS}
