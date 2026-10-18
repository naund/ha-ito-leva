"""Text platform: LCD mirror + active profile (Phase 1 read-only).

The 4x16 LCD frame is exposed as one multiline text entity (glyphs already
mapped in parsers.render_lcd_row) plus the active profile name from row 1.
Editing/menu navigation via virtual buttons is Phase 2 scope.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    async_add_entities(
        [
            ItoLevaLcdMirror(coordinator, device),
            ItoLevaActiveProfile(coordinator, device),
        ]
    )


class _Base(TextEntity):
    _attr_should_poll = False
    _attr_mode = "text"

    def __init__(self, coordinator: ItoLevaCoordinator, device: DeviceInfo) -> None:
        self._coordinator = coordinator
        self._attr_device_info = device

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_TELEMETRY, self._handle_update)
        )

    @callback
    def _handle_update(self, *args) -> None:
        self.async_write_ha_state()

    async def async_set_value(self, value: str) -> None:
        raise NotImplementedError("Read-only in Phase 1 (menu navigation is Phase 2)")


class ItoLevaLcdMirror(_Base):
    _attr_translation_key = "lcd_mirror"
    _attr_icon = "mdi:monitor"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_lcd"

    @property
    def native_value(self) -> str | None:
        rows = (self._coordinator.data.raw or {}).get("rows")
        return "\n".join(rows) if rows else None


class ItoLevaActiveProfile(_Base):
    _attr_translation_key = "active_profile"
    _attr_icon = "mdi:account"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_active_profile"

    @property
    def native_value(self) -> str | None:
        return self._coordinator.data.active_profile
