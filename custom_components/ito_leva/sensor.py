"""Sensor platform (Phase 1, all read-only).

Fast telemetry sensors (pressure/flow/weight/volume) MUST be excluded from
the recorder (see README) — they update at 1 Hz during shots. Setup
sensors come from the MCu tree (refresh via ito_leva.refresh_setup).
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfMass, UnitOfPressure, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SIGNAL_ALARM, SIGNAL_CONNECTION, SIGNAL_TELEMETRY, ItoLevaCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ItoLevaCoordinator = hass.data[DOMAIN][entry.entry_id]
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"ITO Leva! ({coordinator.host})",
        manufacturer="ITO",
        model="Leva!",
    )
    async_add_entities(
        [
            ItoLevaStateSensor(coordinator, device),
            ItoLevaPressureSensor(coordinator, device),
            ItoLevaFlowWeightSensor(coordinator, device),
            ItoLevaFlowMeterSensor(coordinator, device),
            ItoLevaTempSensor(coordinator, device),
            ItoLevaSetpointSensor(coordinator, device),
            ItoLevaWeightSensor(coordinator, device),
            ItoLevaVolumeSensor(coordinator, device),
            ItoLevaTimerSensor(coordinator, device),
            ItoLevaAlarmSensor(coordinator, device),
            ItoLevaConnectionSensor(coordinator, device),
        ]
    )


class ItoLevaSensor(SensorEntity):
    """Base: subscribes to coordinator signals, reads coordinator.data."""

    _attr_should_poll = False

    def __init__(self, coordinator: ItoLevaCoordinator, device: DeviceInfo) -> None:
        self._coordinator = coordinator
        self._attr_device_info = device

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_TELEMETRY, self._handle_update
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONNECTION, self._handle_update
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ALARM, self._handle_update)
        )

    @callback
    def _handle_update(self, *args) -> None:
        self.async_write_ha_state()


class ItoLevaStateSensor(ItoLevaSensor):
    _attr_translation_key = "state"
    _attr_icon = "mdi:coffee-maker"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_state"

    @property
    def native_value(self):
        return self._coordinator.data.state


class ItoLevaPressureSensor(ItoLevaSensor):
    _attr_translation_key = "pressure"
    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPressure.BAR
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_pressure"

    @property
    def native_value(self):
        return self._coordinator.data.pressure


class ItoLevaFlowWeightSensor(ItoLevaSensor):
    """Flow from the scale/Waage in g/s (derived from weight deltas)."""

    _attr_translation_key = "flow_weight"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "g/s"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_flow_weight"

    @property
    def native_value(self):
        return self._coordinator.data.flow


class ItoLevaFlowMeterSensor(ItoLevaSensor):
    """Flow from the flow meter in ml/s (rich-frame / LCD R= field)."""

    _attr_translation_key = "flow_meter"
    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "mL/s"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_flow_meter"

    @property
    def native_value(self):
        return self._coordinator.data.flow_ml


class ItoLevaTempSensor(ItoLevaSensor):
    _attr_translation_key = "temp"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_temp"

    @property
    def native_value(self):
        return self._coordinator.data.temp


class ItoLevaSetpointSensor(ItoLevaSensor):
    """Setpoint mirror (read-only display; editing goes via number entity)."""

    _attr_translation_key = "setpoint"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_setpoint"

    @property
    def native_value(self):
        return self._coordinator.data.setpoint


class ItoLevaWeightSensor(ItoLevaSensor):
    """Cup weight from the Bluetooth scale (DE: Waage)."""

    _attr_translation_key = "weight"
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_weight"

    @property
    def native_value(self):
        return self._coordinator.data.weight


class ItoLevaVolumeSensor(ItoLevaSensor):
    _attr_translation_key = "volume"
    _attr_device_class = SensorDeviceClass.VOLUME
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_volume"

    @property
    def native_value(self):
        return self._coordinator.data.volume


class ItoLevaTimerSensor(ItoLevaSensor):
    _attr_translation_key = "timer"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "s"
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_timer"

    @property
    def native_value(self):
        return self._coordinator.data.timer


class ItoLevaAlarmSensor(ItoLevaSensor):
    """Last firmware alarm token ([REFILL], [DSCALE]=Entkalken, …)."""

    _attr_translation_key = "alarm"
    _attr_icon = "mdi:alert"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_alarm"

    @property
    def native_value(self):
        return self._coordinator.data.alarm or "OK"


class ItoLevaConnectionSensor(ItoLevaSensor):
    _attr_translation_key = "connection"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_connection"

    @property
    def native_value(self):
        return "connected" if self._coordinator.connected else "disconnected"
