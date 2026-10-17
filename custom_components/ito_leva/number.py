"""Number platform: setpoint control (Phase 1).

The firmware only supports RELATIVE setpoint changes (MC+ / MC- = 0.1 C),
so this entity converts an absolute target into N discrete steps from the
last known setpoint. Range 80-100 C covers all sane espresso setpoints.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SIGNAL_TELEMETRY, ItoLevaCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ItoLevaCoordinator = hass.data[DOMAIN][entry.entry_id]
    device = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
    async_add_entities([ItoLevaSetpoint(coordinator, device)])


class ItoLevaSetpoint(NumberEntity):
    _attr_translation_key = "setpoint"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 80.0
    _attr_native_max_value = 100.0
    _attr_native_step = 0.1
    _attr_mode = NumberMode.SLIDER
    _attr_should_poll = False
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: ItoLevaCoordinator, device: DeviceInfo) -> None:
        self._coordinator = coordinator
        self._attr_device_info = device
        self._attr_unique_id = f"{DOMAIN}_setpoint_number"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_TELEMETRY, self._handle_update)
        )

    def _handle_update(self, *args) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._coordinator.data.setpoint

    async def async_set_native_value(self, value: float) -> None:
        current = self._coordinator.data.setpoint
        if current is None:
            # Unknown base: single nudge in the right direction is all we can do.
            await self._coordinator.client.adjust_setpoint(1 if value > 90 else -1)
            return
        steps = round((value - current) / 0.1)
        if steps:
            await self._coordinator.client.adjust_setpoint(steps)
