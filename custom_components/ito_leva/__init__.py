"""Integration setup: one coordinator (one TCP connection) per config entry.

Forwarded platforms are sensor/button/number/text (const.PLATFORMS).
Services (services.yaml):
* refresh_setup — MCu re-read under the client lock (read-only Phase 1)
* start_shot / stop_shot — with the 3 s watchdog (see button.py)
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady

from . import parsers
from .const import DOMAIN, PLATFORMS
from .coordinator import ItoLevaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Open the TCP connection and forward platforms + services."""
    host: str = entry.data["host"]
    port: int = entry.data["port"]
    coordinator = ItoLevaCoordinator(hass, host, port)

    try:
        await coordinator.async_start()
    except (OSError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"ITO at {host}:{port} unreachable: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, list(PLATFORMS))

    async def _handle_refresh_setup(_call: ServiceCall) -> None:
        """Service ito_leva.refresh_setup: MCu dump -> setup_tree."""
        lines = await coordinator.client.dump_setup()
        coordinator.setup_tree = parsers.parse_mcu_tree(lines)
        _LOGGER.info("Setup tree refreshed (%d lines)", len(lines))

    hass.services.async_register(DOMAIN, "refresh_setup", _handle_refresh_setup)
    hass.services.async_register(
        DOMAIN, "start_shot", lambda call: coordinator.client.start_shot()
    )
    hass.services.async_register(
        DOMAIN, "stop_shot", lambda call: coordinator.client.stop_shot()
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Close the TCP connection and unload platforms."""
    coordinator: ItoLevaCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.async_stop()
    hass.services.async_remove(DOMAIN, "refresh_setup")
    hass.services.async_remove(DOMAIN, "start_shot")
    hass.services.async_remove(DOMAIN, "stop_shot")
    return await hass.config_entries.async_unload_platforms(entry, list(PLATFORMS))
