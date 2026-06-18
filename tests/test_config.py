"""
Unit tests for utils.config.

These validate loading, override-merging, and validation behavior of
``load_config()``. Nothing here touches image files or analysis code —
none exists yet in Milestone 1.
"""

from pathlib import Path

import pytest
import yaml

from utils.config import ConfigError, load_config


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_default_config_loads_successfully():
    config = load_config()

    assert config.io.supported_extensions
    assert config.logging.level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    assert config.thresholds.blur_score_min > 0
    total_weight = (
        config.scoring_weights.blur_weight
        + config.scoring_weights.face_weight
        + config.scoring_weights.exposure_weight
    )
    assert 0.99 <= total_weight <= 1.01


def test_override_merges_on_top_of_defaults(tmp_path: Path):
    override_file = _write_yaml(tmp_path / "override.yaml", {"logging": {"level": "DEBUG"}})

    config = load_config(override_path=override_file)

    assert config.logging.level == "DEBUG"
    # Sections untouched by the override should still come from defaults.
    assert config.io.supported_extensions
    assert config.io.output_folder_name == "PhotoFlow_Output"


def test_missing_override_file_raises_config_error(tmp_path: Path):
    missing = tmp_path / "does_not_exist.yaml"

    with pytest.raises(ConfigError):
        load_config(override_path=missing)


def test_malformed_yaml_raises_config_error(tmp_path: Path):
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("io: [unbalanced: brackets", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(override_path=bad_file)


def test_section_overridden_to_non_mapping_raises_config_error(tmp_path: Path):
    override_file = _write_yaml(tmp_path / "override.yaml", {"logging": None})

    with pytest.raises(ConfigError):
        load_config(override_path=override_file)


def test_scoring_weights_must_sum_to_one(tmp_path: Path):
    override_file = _write_yaml(
        tmp_path / "override.yaml",
        {"scoring_weights": {"blur_weight": 0.9, "face_weight": 0.9, "exposure_weight": 0.9}},
    )

    with pytest.raises(ConfigError):
        load_config(override_path=override_file)


def test_supported_extensions_must_start_with_dot(tmp_path: Path):
    override_file = _write_yaml(tmp_path / "override.yaml", {"io": {"supported_extensions": ["jpg"]}})

    with pytest.raises(ConfigError):
        load_config(override_path=override_file)


def test_invalid_log_level_raises_config_error(tmp_path: Path):
    override_file = _write_yaml(tmp_path / "override.yaml", {"logging": {"level": "NOT_A_LEVEL"}})

    with pytest.raises(ConfigError):
        load_config(override_path=override_file)


def test_negative_worker_pool_size_raises_config_error(tmp_path: Path):
    override_file = _write_yaml(tmp_path / "override.yaml", {"performance": {"worker_pool_size": -1}})

    with pytest.raises(ConfigError):
        load_config(override_path=override_file)


def test_worker_pool_size_null_is_allowed():
    # null/None means "auto-detect" and must be accepted, not coerced to an error.
    config = load_config()
    assert config.performance.worker_pool_size is None
