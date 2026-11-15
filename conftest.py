"""Pytest bootstrap.

The Home Assistant test plugin is only required for
``tests/integration``; it is loaded when the project venv is used and
ignored by a plain system-Python run (where those tests are skipped).
"""

from __future__ import annotations

try:
    import pytest_homeassistant_custom_component  # noqa: F401
except ModuleNotFoundError:
    pass
else:
    pytest_plugins = "pytest_homeassistant_custom_component"
