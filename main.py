"""
PhotoFlow command-line entry point (Milestone 2).

Runs the full pipeline over a folder of photos:

    scan  ->  duplicate detection  ->  blur detection  ->  quality scoring
          ->  organize

Usage:
    python main.py PHOTO_FOLDER [--output DIR] [--dry-run] [--config PATH]

Examples:
    # Preview what would happen, without copying anything:
    python main.py "C:/Users/me/Pictures/Trip" --dry-run

    # Organize copies into <folder>/PhotoFlow_Output:
    python main.py "C:/Users/me/Pictures/Trip"

    # Organize into a different destination, with a config override:
    python main.py ./photos --output ./sorted --config ./my_config.yaml

Originals are always copied (never moved or deleted). Best-shot selection is
not implemented as a folder yet, but the highest-quality keeper of each
duplicate group is reported as a best-shot pick.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# Load .env from the project root so OPENAI_API_KEY (and any other secrets)
# are available as environment variables before any module reads them.
try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed; env vars must be set manually.

from core.pipeline import PhotoFlowPipeline, PipelineError, PipelineResult
from utils.config import ConfigError, load_config
from utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="photoflow",
        description="Organize a folder of photos into Duplicates / Blurry / Review.",
    )
    parser.add_argument(
        "folder",
        type=str,
        help="Path to the folder of photos to analyze and organize.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        metavar="DIR",
        help="Directory in which to create PhotoFlow_Output (default: the photo folder).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the classification and counts without copying any files.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help="Optional YAML file overriding default config values.",
    )
    return parser.parse_args(argv)


def _print_summary(result: PipelineResult) -> None:
    """Render a concise human-readable summary to stdout."""
    mode = "DRY RUN (no files copied)" if result.dry_run else "Organized"
    print()
    print(f"PhotoFlow -- {mode}")
    print(f"  Input folder:     {result.input_folder}")
    print(f"  Images scanned:   {result.scanned_count}")
    print(
        f"  Duplicate groups: {result.duplicate_group_count} "
        f"({result.duplicate_count} duplicate file(s))"
    )
    print(f"  Blurry images:    {result.blurry_count}")
    print(f"  Images w/ faces:  {result.faces_detected_count}")
    if result.quality_results:
        avg = sum(q.quality_score for q in result.quality_results) / len(
            result.quality_results
        )
        print(f"  Avg quality:      {avg:.1f}/100 ({len(result.quality_results)} scored)")
    print("  Categorization:")
    for category, count in result.category_counts.items():
        print(f"    - {category:<11} {count}")
    if result.best_shot_candidates:
        print(
            f"  Best-shot picks:  {len(result.best_shot_candidates)} "
            f"(highest-quality keeper per duplicate group)"
        )
    if result.output_root:
        print(f"  Output written to: {result.output_root}")
    if result.blur_failures:
        print(f"  Unreadable images (skipped): {len(result.blur_failures)}")
    print()


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    try:
        config = load_config(override_path=args.config)
    except ConfigError as exc:
        # Logging isn't configured yet if config loading itself fails.
        print(f"PhotoFlow failed to start: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.logging)

    folder = Path(args.folder)
    if not folder.is_dir():
        logger.error("Input folder does not exist or is not a directory: %s", folder)
        print(f"Error: not a folder: {folder}", file=sys.stderr)
        return 1

    pipeline = PhotoFlowPipeline.from_config(config)
    try:
        result = pipeline.run(
            input_folder=folder,
            destination_root=args.output,
            dry_run=args.dry_run,
        )
    except PipelineError as exc:
        logger.error("%s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
