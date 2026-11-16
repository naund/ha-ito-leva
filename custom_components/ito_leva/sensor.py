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

from .const import DOMAIN, ENTITY_IDS
from .coordinator import (
    SIGNAL_ALARM,
    SIGNAL_CONNECTION,
    SIGNAL_SETUP,
    SIGNAL_TELEMETRY,
    ItoLevaCoordinator,
)


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
            ItoLevaStatusSensor(coordinator, device),
            ItoLevaConnectionSensor(coordinator, device),
            ItoLevaSetupControlSensor(coordinator, device),
            ItoLevaSetupSetpointSensor(coordinator, device),
            ItoLevaSetupTempTypeSensor(coordinator, device),
        ]
    )


class ItoLevaSensor(SensorEntity):
    """Base: subscribes to coordinator signals, reads coordinator.data."""

    _attr_should_poll = False
    _attr_has_entity_name = True

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

    @property
    def available(self) -> bool:
        """Unavailable only when the connection switch is off."""
        return self._coordinator.available


class ItoLevaStateSensor(ItoLevaSensor):
    entity_id = ENTITY_IDS["state"]
    _attr_translation_key = "state"
    _attr_icon = "mdi:coffee-maker"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_state"

    @property
    def native_value(self):
        return self._coordinator.data.state


class ItoLevaPressureSensor(ItoLevaSensor):
    entity_id = ENTITY_IDS["pressure"]
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

    entity_id = ENTITY_IDS["flow_weight"]
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

    entity_id = ENTITY_IDS["flow_meter"]
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
    entity_id = ENTITY_IDS["temp"]
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

    entity_id = ENTITY_IDS["setpoint"]
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

    entity_id = ENTITY_IDS["weight"]
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
    entity_id = ENTITY_IDS["volume"]
    _attr_translation_key = "volume"
    _attr_device_class = SensorDeviceClass.VOLUME
    # VOLUME only allows TOTAL/TOTAL_INCREASING; MEASUREMENT is rejected.
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_volume"

    @property
    def native_value(self):
        return self._coordinator.data.volume


class ItoLevaTimerSensor(ItoLevaSensor):
    entity_id = ENTITY_IDS["timer"]
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

    entity_id = ENTITY_IDS["alarm"]
    _attr_translation_key = "alarm"
    _attr_icon = "mdi:alert"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_alarm"

    @property
    def native_value(self):
        return self._coordinator.data.alarm or "OK"


class ItoLevaStatusSensor(ItoLevaSensor):
    """User-facing machine status: READY / HEATING / ECO / STANDBY / …

    Rich mode (the only stream the firmware provides while streaming) has no
    display data, so the status is derived from setpoint and boiler
    temperature - see parsers.derive_status().
    """

    entity_id = ENTITY_IDS["status"]
    _attr_translation_key = "status"

    _STATUS_ICONS = {
        "READY": "mdi:check-circle-outline",
        "HEATING": "mdi:fire",
        "COOLING": "mdi:thermometer-chevron-down",
        "WARM-UP": "mdi:radiator",
        "STEAM": "mdi:pot-steam",
        "ECO": "mdi:leaf",
        "STANDBY": "mdi:power-sleep",
        "BREWING": "mdi:coffee",
        "WAIT": "mdi:timer-sand",
        "OFFLINE": "mdi:lan-disconnect",
    }

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_status"

    @property
    def native_value(self):
        return self._coordinator.data.status

    @property
    def icon(self):
        return self._STATUS_ICONS.get(self._coordinator.data.status, "mdi:coffee-maker")


class ItoLevaConnectionSensor(ItoLevaSensor):
    entity_id = ENTITY_IDS["connection"]
    _attr_translation_key = "connection"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_connection"

    @property
    def available(self) -> bool:
        """Always show whether the TCP link is up, even when detached."""
        return True

    @property
    def native_value(self):
        return "connected" if self._coordinator.connected else "disconnected"


class ItoLevaSetupSensor(ItoLevaSensor):
    """Read-only value from the MCu setup tree (refresh_setup service)."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_SETUP, self._handle_update)
        )

    def _tree_value(self, *path: str):
        node = self._coordinator.setup_tree
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return node if isinstance(node, str) else None


class ItoLevaSetupControlSensor(ItoLevaSetupSensor):
    entity_id = ENTITY_IDS["setup_pid_control"]
    _attr_translation_key = "setup_pid_control"
    _attr_icon = "mdi:tune"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_setup_pid_control"

    @property
    def native_value(self):
        return self._tree_value("PID 1", "Control")


class ItoLevaSetupSetpointSensor(ItoLevaSetupSensor):
    entity_id = ENTITY_IDS["setup_pid_setpoint"]
    _attr_translation_key = "setup_pid_setpoint"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_setup_pid_setpoint"

    @property
    def native_value(self):
        raw = self._tree_value("PID 1", "Setpoint")
        try:
            return float(raw) if raw is not None else None
        except ValueError:
            return None


class ItoLevaSetupTempTypeSensor(ItoLevaSetupSensor):
    entity_id = ENTITY_IDS["setup_temp_type"]
    _attr_translation_key = "setup_temp_type"
    _attr_icon = "mdi:thermometer-check"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{DOMAIN}_setup_temp_type"

    @property
    def native_value(self):
        return self._tree_value("Sensors", "Temperature", "Type")
