"""
Configuration loading and validation for PhotoFlow.

This module is the single source of truth for runtime configuration. It
loads the shipped defaults from ``data/default_config.yaml``, optionally
deep-merges a user-supplied override file on top of it, validates the
result into typed, immutable dataclasses, and returns an ``AppConfig``.

Milestone 1 scope: load + merge + validate only. No component in this
milestone reads ``thresholds``, ``scoring_weights``, or ``performance`` —
those sections exist so the schema is stable and testable ahead of the
analysis modules that will consume them starting in Milestone 2.

Design notes:
- Dataclasses are frozen (immutable) so configuration can't be mutated
  accidentally after load, which matters once multiple worker processes
  read from it in later milestones.
- All validation failures raise ``ConfigError`` with a specific, actionable
  message rather than letting a ``KeyError``/``TypeError`` leak out of a
  YAML-shaped dict, so a bad config fails fast and clearly at startup.
"""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import Any, Optional, Union

import yaml


class ConfigError(Exception):
    """Raised when configuration is missing, malformed, or fails validation."""


@dataclasses.dataclass(frozen=True)
class IOConfig:
    supported_extensions: tuple[str, ...]
    output_folder_name: str
    copy_not_move: bool


@dataclasses.dataclass(frozen=True)
class LoggingConfig:
    level: str
    log_dir: str
    max_bytes: int
    backup_count: int


@dataclasses.dataclass(frozen=True)
class ThresholdsConfig:
    """Analysis thresholds consumed by blur, face, and quality stages."""

    blur_score_min: float
    duplicate_hash_distance_max: int
    face_detection_confidence_min: float
    face_model_path: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class ScoringWeightsConfig:
    """Not yet consumed by any module in Milestone 1; reserved for Milestone 2+."""

    blur_weight: float
    face_weight: float
    exposure_weight: float


@dataclasses.dataclass(frozen=True)
class PerformanceConfig:
    """Not yet consumed by any module in Milestone 1; reserved for Milestone 2+."""

    analysis_max_edge_px: int
    worker_pool_size: Optional[int]


@dataclasses.dataclass(frozen=True)
class AppConfig:
    io: IOConfig
    logging: LoggingConfig
    thresholds: ThresholdsConfig
    scoring_weights: ScoringWeightsConfig
    performance: PerformanceConfig


# Resolves to <project_root>/data/default_config.yaml regardless of the
# caller's current working directory.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "default_config.yaml"

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML in {path}: {exc}") from exc
    if data is None:
        raise ConfigError(f"Config file is empty: {path}")
    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file must contain a mapping at the top level: {path}"
        )
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` on top of `base` without mutating either."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _require_section(data: dict[str, Any], section: str) -> dict[str, Any]:
    if section not in data:
        raise ConfigError(f"Missing required config section: '{section}'")
    value = data[section]
    if not isinstance(value, dict):
        raise ConfigError(
            f"Config section '{section}' must be a mapping, "
            f"got {type(value).__name__}"
        )
    return value


def _require_keys(section_data: dict[str, Any], section_name: str, keys: list[str]) -> None:
    missing = [k for k in keys if k not in section_data]
    if missing:
        raise ConfigError(
            f"Config section '{section_name}' is missing required keys: {missing}"
        )


def load_config(override_path: Optional[Union[str, Path]] = None) -> AppConfig:
    """
    Load, merge, and validate PhotoFlow configuration.

    Args:
        override_path: Optional path to a YAML file whose values are
            deep-merged on top of the shipped defaults. Only the keys
            present in the override file are changed; everything else
            keeps its default value.

    Returns:
        A fully validated, immutable ``AppConfig``.

    Raises:
        ConfigError: if either file is missing/malformed, a required
            section or key is absent, or a value fails validation
            (e.g. scoring weights that don't sum to ~1.0).
    """
    raw = _read_yaml(DEFAULT_CONFIG_PATH)

    if override_path is not None:
        override_raw = _read_yaml(Path(override_path))
        raw = _deep_merge(raw, override_raw)

    io_data = _require_section(raw, "io")
    _require_keys(io_data, "io", ["supported_extensions", "output_folder_name", "copy_not_move"])

    logging_data = _require_section(raw, "logging")
    _require_keys(logging_data, "logging", ["level", "log_dir", "max_bytes", "backup_count"])

    thresholds_data = _require_section(raw, "thresholds")
    _require_keys(
        thresholds_data,
        "thresholds",
        ["blur_score_min", "duplicate_hash_distance_max", "face_detection_confidence_min"],
    )

    scoring_data = _require_section(raw, "scoring_weights")
    _require_keys(scoring_data, "scoring_weights", ["blur_weight", "face_weight", "exposure_weight"])

    performance_data = _require_section(raw, "performance")
    _require_keys(performance_data, "performance", ["analysis_max_edge_px", "worker_pool_size"])

    try:
        config = AppConfig(
            io=IOConfig(
                supported_extensions=tuple(io_data["supported_extensions"]),
                output_folder_name=str(io_data["output_folder_name"]),
                copy_not_move=bool(io_data["copy_not_move"]),
            ),
            logging=LoggingConfig(
                level=str(logging_data["level"]).upper(),
                log_dir=str(logging_data["log_dir"]),
                max_bytes=int(logging_data["max_bytes"]),
                backup_count=int(logging_data["backup_count"]),
            ),
            thresholds=ThresholdsConfig(
                blur_score_min=float(thresholds_data["blur_score_min"]),
                duplicate_hash_distance_max=int(thresholds_data["duplicate_hash_distance_max"]),
                face_detection_confidence_min=float(thresholds_data["face_detection_confidence_min"]),
                face_model_path=(
                    str(thresholds_data["face_model_path"])
                    if thresholds_data.get("face_model_path") is not None
                    else None
                ),
            ),
            scoring_weights=ScoringWeightsConfig(
                blur_weight=float(scoring_data["blur_weight"]),
                face_weight=float(scoring_data["face_weight"]),
                exposure_weight=float(scoring_data["exposure_weight"]),
            ),
            performance=PerformanceConfig(
                analysis_max_edge_px=int(performance_data["analysis_max_edge_px"]),
                worker_pool_size=(
                    None
                    if performance_data["worker_pool_size"] is None
                    else int(performance_data["worker_pool_size"])
                ),
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid value type in config: {exc}") from exc

    _validate_semantics(config)
    return config


def _validate_semantics(config: AppConfig) -> None:
    """Validation rules that go beyond simple type-correctness."""
    if config.logging.level not in _VALID_LOG_LEVELS:
        raise ConfigError(
            f"logging.level must be one of {sorted(_VALID_LOG_LEVELS)}, "
            f"got '{config.logging.level}'"
        )

    if not config.io.supported_extensions:
        raise ConfigError("io.supported_extensions must not be empty")

    for ext in config.io.supported_extensions:
        if not ext.startswith("."):
            raise ConfigError(
                f"io.supported_extensions entries must start with '.', got '{ext}'"
            )

    if config.logging.max_bytes <= 0:
        raise ConfigError("logging.max_bytes must be a positive integer")

    if config.logging.backup_count < 0:
        raise ConfigError("logging.backup_count must be zero or a positive integer")

    weights = config.scoring_weights
    total_weight = weights.blur_weight + weights.face_weight + weights.exposure_weight
    if not (0.99 <= total_weight <= 1.01):
        raise ConfigError(
            f"scoring_weights (blur + face + exposure) must sum to ~1.0, got {total_weight}"
        )

    if config.performance.worker_pool_size is not None and config.performance.worker_pool_size < 1:
        raise ConfigError("performance.worker_pool_size must be a positive integer or null")

    if config.performance.analysis_max_edge_px <= 0:
        raise ConfigError("performance.analysis_max_edge_px must be a positive integer")

    thresholds = config.thresholds
    if not 0.0 <= thresholds.face_detection_confidence_min <= 1.0:
        raise ConfigError(
            "thresholds.face_detection_confidence_min must be between 0.0 and 1.0, "
            f"got {thresholds.face_detection_confidence_min}"
        )
    if thresholds.duplicate_hash_distance_max < 0:
        raise ConfigError(
            "thresholds.duplicate_hash_distance_max must be >= 0, "
            f"got {thresholds.duplicate_hash_distance_max}"
        )
    if thresholds.blur_score_min < 0:
        raise ConfigError(
            f"thresholds.blur_score_min must be >= 0, got {thresholds.blur_score_min}"
        )
