"""Number platform: setpoint control.

The firmware only supports RELATIVE setpoint changes (`MC+` / `MC-`) and the
size of one press is configurable (`MC#` = 0.1, 1.0 or 10 K). This entity
therefore asks the coordinator to walk the target in verified steps instead of
converting an absolute value into a counted burst - a burst based on the wrong
increment (or fired while a program overrides the setpoint) is what made the
target jump to the limits during the field test.

The displayed value is always the machine's own read-back, so the UI cannot
drift away from what the firmware actually holds.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENTITY_IDS, SETPOINT_MAX, SETPOINT_MIN
from .coordinator import SIGNAL_TELEMETRY, ItoLevaCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ItoLevaCoordinator = hass.data[DOMAIN][entry.entry_id]
    device = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
    async_add_entities([ItoLevaSetpoint(coordinator, device)])


class ItoLevaSetpoint(NumberEntity):
    entity_id = ENTITY_IDS["setpoint_number"]
    _attr_has_entity_name = True
    _attr_translation_key = "setpoint"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = SETPOINT_MIN
    _attr_native_max_value = SETPOINT_MAX
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: ItoLevaCoordinator, device: DeviceInfo) -> None:
        self._coordinator = coordinator
        self._attr_device_info = device
        self._attr_unique_id = f"{DOMAIN}_setpoint_number"

    @property
    def available(self) -> bool:
        return self._coordinator.available

    @property
    def native_step(self) -> float:
        """Offer only values the firmware can actually reach.

        The press size is measured on the first write (MC# is 0.1/1.0/10 K);
        until then the finest possible step is used.
        """
        return self._coordinator.setpoint_increment or 0.1

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_TELEMETRY, self._handle_update)
        )

    @callback
    def _handle_update(self, *args) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        """The machine's own value (may be a program override, e.g. warm-up)."""
        return self._coordinator.data.setpoint

    @property
    def extra_state_attributes(self) -> dict:
        """Expose why the value cannot be changed right now (UI + automations).

        Warm-up and steam are normal operating phases; the firmware refuses
        setpoint keys while they run, so the entity reports the lock instead of
        pretending the request failed.
        """
        reason = self._coordinator.setpoint_lock_reason()
        return {"locked": reason is not None, "lock_reason": reason}

    async def async_set_native_value(self, value: float) -> None:
        await self._coordinator.async_set_setpoint(value)
        # Reflect the verified read-back (or the nearest reachable value)
        # immediately instead of waiting for the next 1 Hz push.
        self.async_write_ha_state()
