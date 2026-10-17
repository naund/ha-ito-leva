"""Button platform (Phase 1 daily operation + cleaning).

* start_shot: fires MCcDOSE/Shortcut[1] via client.start_shot(), then the
  3 s watchdog waits for PULLING/BREWING; on timeout a
  persistent_notification is raised (shot error).
* stop_shot: OK button (\\x03).
* flush / warmup / pump_pb / backflush / descale: single-shot MCc commands,
  no lock needed (milliseconds).
* wake / standby / eco: power-state helpers replacing the old YAML telnet
  switches (MCa / MCE / MCe).

DE labels live in translations/de.json (Descale -> Entkalken).
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .const import (
    CMD_DOSE,
    CMD_ECO,
    CMD_FLUSH,
    CMD_PUMP_PB,
    CMD_STANDBY,
    CMD_WAKE,
    CMD_WARMUP,
    DOMAIN,
    SHOT_WATCHDOG_TIMEOUT,
)
from .coordinator import ItoLevaCoordinator

_LOGGER = logging.getLogger(__name__)

# MCc program for limescale removal. The firmware command name is what the
# assignable-function list (manual p.229-230, MCC_MAP) exposes; if a given
# firmware uses a different token, only this constant changes.
CMD_DESCALE = "MCcDESCALE\r"
CMD_BACKFLUSH = "MCcBACKFLUSH\r"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ItoLevaCoordinator = hass.data[DOMAIN][entry.entry_id]
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"ITO Leva! ({coordinator.host})",
    )
    async_add_entities(
        [
            ItoLevaStartShot(coordinator, device),
            ItoLevaStopShot(coordinator, device),
            ItoLevaSimpleButton(coordinator, device, "flush", CMD_FLUSH, "mdi:shower"),
            ItoLevaSimpleButton(coordinator, device, "warmup", CMD_WARMUP, "mdi:sun-thermometer"),
            ItoLevaSimpleButton(coordinator, device, "pump_pb", CMD_PUMP_PB, "mdi:pump"),
            ItoLevaSimpleButton(coordinator, device, "backflush", CMD_BACKFLUSH, "mdi:coffee-maker"),
            ItoLevaSimpleButton(coordinator, device, "descale", CMD_DESCALE, "mdi:bottle-tonic"),
            ItoLevaSimpleButton(coordinator, device, "wake", CMD_WAKE, "mdi:power"),
            ItoLevaSimpleButton(coordinator, device, "standby", CMD_STANDBY, "mdi:power-sleep"),
            ItoLevaSimpleButton(coordinator, device, "eco", CMD_ECO, "mdi:leaf"),
            ItoLevaSimpleButton(coordinator, device, "dose", CMD_DOSE, "mdi:cup"),
        ]
    )


class ItoLevaSimpleButton(ButtonEntity):
    """Fire-and-forget MCc command (translation_key -> translations/*.json)."""

    _attr_should_poll = False

    def __init__(
        self, coordinator: ItoLevaCoordinator, device: DeviceInfo,
        key: str, command: str, icon: str,
    ) -> None:
        self._coordinator = coordinator
        self._command = command
        self._attr_device_info = device
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_unique_id = f"{DOMAIN}_{key}"

    async def async_press(self) -> None:
        self._coordinator.client.send_command(self._command)


class ItoLevaStartShot(ButtonEntity):
    """Start shot with watchdog: 3 s to reach PULLING/BREWING, else notify."""

    _attr_translation_key = "start_shot"
    _attr_icon = "mdi:play"
    _attr_should_poll = False

    def __init__(self, coordinator: ItoLevaCoordinator, device: DeviceInfo) -> None:
        self._coordinator = coordinator
        self._attr_device_info = device
        self._attr_unique_id = f"{DOMAIN}_start_shot"

    async def async_press(self) -> None:
        client = self._coordinator.client
        await client.start_shot()
        await asyncio.sleep(SHOT_WATCHDOG_TIMEOUT)
        state = self._coordinator.data.state
        alarm = self._coordinator.data.alarm
        if state not in ("PULLING", "BREWING"):
            reason = parsers.ALARM_REASONS.get(alarm, "kein PULLING/BREWING erreicht")
            if alarm:
                reason = parsers.ALARM_REASONS.get(alarm, alarm)
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "ITO Leva! Start fehlgeschlagen",
                    "message": f"Kein Shot-Start nach 3 s (state={state}). Grund: {reason}.",
                    "notification_id": f"{DOMAIN}_start_failed",
                },
                blocking=False,
            )


class ItoLevaStopShot(ButtonEntity):
    _attr_translation_key = "stop_shot"
    _attr_icon = "mdi:stop"
    _attr_should_poll = False

    def __init__(self, coordinator: ItoLevaCoordinator, device: DeviceInfo) -> None:
        self._coordinator = coordinator
        self._attr_device_info = device
        self._attr_unique_id = f"{DOMAIN}_stop_shot"

    async def async_press(self) -> None:
        await self._coordinator.client.stop_shot()
