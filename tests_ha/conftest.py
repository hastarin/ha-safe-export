"""Fixtures for the Safe Export integration tests.

These need Home Assistant's test harness, which needs a newer Python than the
rest of the project and does not run on Windows: install the `ha` extra in its
own Linux/WSL venv (see custom_components/safe_export/README.md), then run
`pytest tests_ha`. Without the harness every test module here skips.
"""

from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("pytest_homeassistant_custom_component") is not None:
    from .common import (  # noqa: F401 — registers the fixtures
        auto_enable_custom_integrations,
        config_entry,
        setup_entry,
    )


def pytest_configure(config: pytest.Config) -> None:
    """The harness's async fixtures (hass, …) need pytest-asyncio's auto mode.

    Set here rather than in pyproject.toml so the main suite, which runs without
    pytest-asyncio, does not warn about an unknown ini option. pytest loads this
    conftest before configuring whenever tests_ha is on the command line.
    """
    config.option.asyncio_mode = "auto"
