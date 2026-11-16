"""Integration setup: one coordinator (one TCP connection) per config entry.

Forwarded platforms are sensor/button/number/text (const.PLATFORMS).
Services (services.yaml):
* refresh_setup — MCu re-read under the client lock (read-only Phase 1)
* start_shot / stop_shot — with the 3 s watchdog (see button.py)
"""

from __future__ import annotations

import logging
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import CONNECT_TIMEOUT, DOMAIN, ENTITY_IDS, PLATFORMS
from .coordinator import ItoLevaCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICES = ("refresh_setup", "start_shot", "stop_shot", "export_recent")


def _active_coordinator(hass: HomeAssistant) -> ItoLevaCoordinator:
    """Return the coordinator of the configured machine.

    The manifest declares `single_config_entry`, but resolving dynamically
    keeps the service handlers correct even if an entry is unloading.
    """
    coordinators = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise HomeAssistantError("ITO Leva! ist nicht eingerichtet")
    return next(iter(coordinators.values()))


async def _handle_refresh_setup(hass: HomeAssistant, _call: ServiceCall) -> None:
    """Service ito_leva.refresh_setup: MCu dump -> setup_tree."""
    await _active_coordinator(hass).async_refresh_setup()


async def _handle_start_shot(hass: HomeAssistant, _call: ServiceCall) -> None:
    """Service ito_leva.start_shot: same sequence as the start button."""
    await _active_coordinator(hass).client.start_shot()


async def _handle_stop_shot(hass: HomeAssistant, _call: ServiceCall) -> None:
    """Service ito_leva.stop_shot: same sequence as the stop button."""
    await _active_coordinator(hass).client.stop_shot()


async def _handle_export_recent(hass: HomeAssistant, call: ServiceCall) -> dict:
    """Service ito_leva.export_recent: write the raw ring buffer to CSV."""
    minutes = call.data.get("minutes") or None
    path, frames = await _active_coordinator(hass).async_export_recent(minutes)
    return {"path": path, "frames": frames}


def _register_services(hass: HomeAssistant) -> None:
    """Register the global services exactly once (not per config entry)."""
    handlers = {
        "refresh_setup": _handle_refresh_setup,
        "start_shot": _handle_start_shot,
        "stop_shot": _handle_stop_shot,
        "export_recent": _handle_export_recent,
    }
    for name, handler in handlers.items():
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN,
                name,
                partial(handler, hass),
                supports_response=SupportsResponse.OPTIONAL,
            )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v1 host-based entity IDs to the stable dashboard IDs."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        registry = er.async_get(hass)
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
            if reg.platform != DOMAIN:
                continue
            suffix = reg.unique_id.removeprefix(f"{DOMAIN}_")
            target = ENTITY_IDS.get(suffix)
            if not target or reg.entity_id == target:
                continue
            if registry.async_get(target) is not None:
                _LOGGER.warning(
                    "Cannot migrate %s to %s: target ID already exists",
                    reg.entity_id,
                    target,
                )
                continue
            registry.async_update_entity(reg.entity_id, new_entity_id=target)
        hass.config_entries.async_update_entry(entry, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Open the TCP connection and forward platforms + services."""
    host: str = entry.data["host"]
    port: int = entry.data["port"]
    coordinator = ItoLevaCoordinator(hass, entry.entry_id, host, port)

    # The connect/disconnect switch is persisted; a detached entry must load
    # without a connection (and without failing with ConfigEntryNotReady).
    await coordinator.async_load_connection_state()
    if coordinator.connection_enabled:
        await coordinator.async_start()
        if not await coordinator.client.wait_ready(CONNECT_TIMEOUT):
            await coordinator.async_stop()
            raise ConfigEntryNotReady(
                f"ITO at {host}:{port} did not reach the rich telemetry handshake"
            )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, list(PLATFORMS))

    if coordinator.connection_enabled and coordinator.eco_setpoint is None:
        # One read-only MCu dump gives the eco setpoint (status ECO) and
        # populates the setup sensors; the result is cached in the store so
        # this only runs on the first start.
        entry.async_create_background_task(
            hass,
            coordinator.async_initial_setup_refresh(),
            "ito_leva_initial_setup",
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Close the TCP connection and unload platforms."""
    coordinator: ItoLevaCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.async_stop()
    if not hass.data[DOMAIN]:
        # Services are global; only remove them when the last entry is gone.
        for name in SERVICES:
            hass.services.async_remove(DOMAIN, name)
    return await hass.config_entries.async_unload_platforms(entry, list(PLATFORMS))
