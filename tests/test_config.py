"""Tests for src/config.py — config loading and the example template.

The example-config test guards the "schema evolved, example forgotten" regression
class: config.example.yaml must always load with the current required keys
(it shipped broken between model v1.4.0 and 2026-07-03 — missing warm_boundary_*).
"""

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from src.config import load_config

EXAMPLE_CONFIG = Path("config/config.example.yaml")


def test_example_config_loads():
    cfg = load_config(EXAMPLE_CONFIG)
    assert cfg.battery_capacity_wh > 0
    assert 0.0 <= cfg.battery_reserve_fraction < 1.0
    assert isinstance(cfg.timezone, ZoneInfo)
    assert cfg.providers, "example must define at least one provider period"


def test_example_config_has_all_model_fields():
    # Every ModelConfig field must be present and numeric in the example —
    # a placeholder value is fine, a missing key is not.
    cfg = load_config(EXAMPLE_CONFIG)
    m = cfg.model
    assert m.warm_boundary_p50 > 0
    assert m.warm_boundary_p50 <= m.warm_boundary_p75 <= m.warm_boundary_p90 <= m.warm_boundary_p95
    assert m.mild_p50 <= m.mild_p75 <= m.mild_p90 <= m.mild_p95
    assert m.heating_p95_buffer_kwh > 0
    assert m.cooling_p95_buffer_kwh > 0


def _write_config(tmp_path: Path, raw: dict) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(raw), encoding="utf-8")
    return config_path


def test_missing_scalar_key_raises_value_error(tmp_path: Path):
    raw = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    del raw["model"]["warm_boundary_p50"]
    config_path = _write_config(tmp_path, raw)

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)

    message = str(exc_info.value)
    assert str(config_path) in message
    assert "model.warm_boundary_p50" in message


def test_missing_section_raises_value_error(tmp_path: Path):
    raw = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    del raw["model"]
    config_path = _write_config(tmp_path, raw)

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)

    message = str(exc_info.value)
    assert str(config_path) in message
    assert "model" in message
