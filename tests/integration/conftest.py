"""HA integration test harness for the ITO Leva! custom component.

The integration is copied into a throw-away config dir per test, so Home
Assistant's custom-component loader sees it without a symlink into the
virtualenv. The fake device speaks the real MC framing.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

INTEGRATION_SRC = (
    Path(__file__).resolve().parents[2] / "custom_components" / "ito_leva"
)

# Let `from fake_ito import FakeIto` work without making tests a package.
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components from the per-test config dir."""
    yield


@pytest.fixture(autouse=True)
def auto_enable_sockets(socket_enabled):
    """Allow real TCP connections to the fake device."""
    yield


@pytest.fixture
def hass_config_dir(tmp_path):
    """Provide a config dir containing this integration."""
    config = tmp_path / "config"
    (config / "custom_components").mkdir(parents=True)
    shutil.copytree(
        INTEGRATION_SRC,
        config / "custom_components" / "ito_leva",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (config / "configuration.yaml").write_text("", encoding="utf-8")
    return str(config)
