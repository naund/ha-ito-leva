"""Switch platform: attach/detach the integration from the machine.

The integration keeps one persistent TCP connection to the module. Before this
switch existed the only way to stop that was to disable the whole config entry.
Turning the switch off closes the socket and stops the reconnect loop while the
entry, its entities and the dashboard IDs stay in place; the choice is
persisted, so a Home Assistant restart does not silently reconnect.

Entities other than the connection sensor and this switch become unavailable
while detached - that is how you see "not connected" instead of stale values.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENTITY_IDS
from .coordinator import SIGNAL_CONNECTION, ItoLevaCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ItoLevaCoordinator = hass.data[DOMAIN][entry.entry_id]
    device = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
    async_add_entities([ItoLevaConnectionSwitch(coordinator, device)])


class ItoLevaConnectionSwitch(SwitchEntity):
    """Keep the TCP connection open (on) or detach the machine (off)."""

    entity_id = ENTITY_IDS["connection_switch"]
    _attr_has_entity_name = True
    _attr_translation_key = "connection"
    _attr_should_poll = False

    def __init__(self, coordinator: ItoLevaCoordinator, device: DeviceInfo) -> None:
        self._coordinator = coordinator
        self._attr_device_info = device
        self._attr_unique_id = f"{DOMAIN}_connection_switch"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONNECTION, self._handle_update
            )
        )

    @callback
    def _handle_update(self, *args) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """The switch must always be reachable - it is the way back."""
        return True

    @property
    def is_on(self) -> bool:
        return self._coordinator.connection_enabled

    @property
    def icon(self) -> str:
        return "mdi:lan-connect" if self.is_on else "mdi:lan-disconnect"

    async def async_turn_on(self, **kwargs) -> None:
        await self._coordinator.async_set_connection(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._coordinator.async_set_connection(False)
