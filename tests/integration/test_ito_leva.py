"""Home Assistant integration tests for the ha-ito-leva custom component.

The device is simulated by ``fake_ito.FakeIto`` (same MC protocol framing as the
real firmware). Tests drive the integration through HA services and inspect the
entity registry/states, i.e. exactly what a user would see.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from fake_ito import FakeIto

DOMAIN = "ito_leva"

SENSOR_IDS = {
    "ito_leva_state": "sensor.ito_leva_state",
    "ito_leva_pressure": "sensor.ito_leva_pressure",
    "ito_leva_flow_weight": "sensor.ito_leva_flow_weight",
    "ito_leva_flow_meter": "sensor.ito_leva_flow_meter",
    "ito_leva_temp": "sensor.ito_leva_temp",
    "ito_leva_setpoint": "sensor.ito_leva_setpoint",
    "ito_leva_weight": "sensor.ito_leva_weight",
    "ito_leva_volume": "sensor.ito_leva_volume",
    "ito_leva_timer": "sensor.ito_leva_timer",
    "ito_leva_alarm": "sensor.ito_leva_alarm",
    "ito_leva_connection": "sensor.ito_leva_connection",
    "ito_leva_setup_pid_control": "sensor.ito_leva_setup_pid_control",
    "ito_leva_setup_pid_setpoint": "sensor.ito_leva_setup_pid_setpoint",
    "ito_leva_setup_temp_type": "sensor.ito_leva_setup_temp_type",
}


# ---------------------------------------------------------------- helpers ----
async def setup_entry(hass, fake: FakeIto, host: str = "127.0.0.1"):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": host, "port": fake.port}
    )
    await hass.async_block_till_done()
    return hass.config_entries.async_entries(DOMAIN)[-1]


async def wait_for_frames(hass, fake: FakeIto, minimum: int = 10, timeout: float = 8.0):
    """Wait until frames arrive, then let the 1 Hz throttle push to entities."""
    end = time.monotonic() + timeout
    while fake.frames_sent < minimum and time.monotonic() < end:
        await asyncio.sleep(0.05)
    await asyncio.sleep(1.1)
    await hass.async_block_till_done()


def entity_id_for(hass, entry, unique_id: str) -> str:
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.unique_id == unique_id:
            return item.entity_id
    raise AssertionError(f"no entity for unique_id {unique_id!r}")


def state_of(hass, entry, unique_id: str):
    return hass.states.get(entity_id_for(hass, entry, unique_id))


async def press(hass, entry, unique_id: str):
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id_for(hass, entry, unique_id)},
        blocking=True,
    )
    await hass.async_block_till_done()
    # Let the fake device read the bytes off the socket.
    await asyncio.sleep(0.15)


@pytest.fixture
async def fake():
    device = FakeIto()
    await device.start()
    yield device
    await device.stop()


@pytest.fixture
async def entry(hass, fake):
    ent = await setup_entry(hass, fake)
    yield ent
    if ent.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(ent.entry_id)
        await hass.async_block_till_done()


# ------------------------------------------------------------------ tests ----
async def test_config_flow_and_entity_inventory(hass, entry, fake):
    """Config flow creates a loaded entry with the expected entities."""
    assert entry.state is ConfigEntryState.LOADED
    assert entry.title == "ITO Leva! (127.0.0.1)"
    assert entry.data == {"host": "127.0.0.1", "port": fake.port}

    registry = er.async_get(hass)
    items = er.async_entries_for_config_entry(registry, entry.entry_id)
    by_domain: dict[str, int] = {}
    for item in items:
        by_domain[item.domain] = by_domain.get(item.domain, 0) + 1
    assert by_domain == {"sensor": 14, "button": 11, "number": 1, "text": 2}

    assert {item.unique_id for item in items} == {
        *SENSOR_IDS,
        "ito_leva_start_shot",
        "ito_leva_stop_shot",
        "ito_leva_flush",
        "ito_leva_warmup",
        "ito_leva_pump_pb",
        "ito_leva_backflush",
        "ito_leva_descale",
        "ito_leva_wake",
        "ito_leva_standby",
        "ito_leva_eco",
        "ito_leva_dose",
        "ito_leva_setpoint_number",
        "ito_leva_lcd",
        "ito_leva_active_profile",
    }


async def test_dashboard_entity_ids_and_friendly_names(hass, entry):
    """Entity IDs match the shipped dashboard; names are localised."""
    for unique_id, expected in SENSOR_IDS.items():
        assert entity_id_for(hass, entry, unique_id) == expected

    assert entity_id_for(hass, entry, "ito_leva_setpoint_number") == (
        "number.ito_leva_setpoint"
    )
    assert entity_id_for(hass, entry, "ito_leva_lcd") == (
        "text.ito_leva_lcd_mirror"
    )
    assert entity_id_for(hass, entry, "ito_leva_active_profile") == (
        "text.ito_leva_active_profile"
    )
    assert entity_id_for(hass, entry, "ito_leva_start_shot") == (
        "button.ito_leva_start_shot"
    )

    device_name = "ITO Leva! (127.0.0.1)"
    names: set[str] = set()
    items = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    for item in items:
        assert item.has_entity_name is True
        assert item.original_name is not None
        state = hass.states.get(item.entity_id)
        assert state is not None
        friendly = state.attributes["friendly_name"]
        assert friendly != device_name
        assert device_name in friendly
        names.add(friendly)
    assert len(names) == len(items)


async def test_rich_telemetry_populates_entities(hass, entry, fake):
    await wait_for_frames(hass, fake)

    assert state_of(hass, entry, "ito_leva_state").state == "IDLE"
    assert float(state_of(hass, entry, "ito_leva_temp").state) == pytest.approx(93.5)
    assert float(state_of(hass, entry, "ito_leva_pressure").state) == pytest.approx(0.2)
    assert float(state_of(hass, entry, "ito_leva_volume").state) == pytest.approx(0.0)
    assert float(state_of(hass, entry, "ito_leva_timer").state) == pytest.approx(0.0)
    assert float(state_of(hass, entry, "ito_leva_flow_meter").state) == pytest.approx(0.0)
    assert state_of(hass, entry, "ito_leva_connection").state == "connected"
    assert state_of(hass, entry, "ito_leva_weight").state == "unknown"

    # Vendor Setpoint field (at=13) is parsed into both entities.
    assert float(state_of(hass, entry, "ito_leva_setpoint").state) == pytest.approx(94.0)
    assert float(
        state_of(hass, entry, "ito_leva_setpoint_number").state
    ) == pytest.approx(94.0)
    assert state_of(hass, entry, "ito_leva_active_profile").state == "TestProf"


async def test_setpoint_number_uses_parsed_base(hass, entry, fake):
    await wait_for_frames(hass, fake)
    eid = entity_id_for(hass, entry, "ito_leva_setpoint_number")
    before = fake.commands.count(b"MC+")

    await hass.services.async_call(
        "number", "set_value", {"entity_id": eid, "value": 95.0}, blocking=True
    )
    await hass.async_block_till_done()

    # 94.0 -> 95.0 is exactly ten 0.1 C steps, not a single blind nudge.
    assert fake.commands.count(b"MC+") - before == 10
    await wait_for_frames(hass, fake, fake.frames_sent + 5)
    assert float(hass.states.get(eid).state) == pytest.approx(95.0)


async def test_start_and_stop_shot(hass, entry, fake):
    await wait_for_frames(hass, fake)
    notifications = async_mock_service(hass, "persistent_notification", "create")
    before_shortcut = fake.commands.count(b"MC\x05")

    await press(hass, entry, "ito_leva_start_shot")
    # Start now fires the active dose; Shortcut[1] is FLUSH and must not be used.
    assert fake.sent(b"MCcDOSE")
    assert fake.commands.count(b"MC\x05") == before_shortcut
    assert fake.shot is True
    await wait_for_frames(hass, fake, fake.frames_sent + 5)
    assert state_of(hass, entry, "ito_leva_state").state == "PULLING"
    assert notifications == []

    await press(hass, entry, "ito_leva_stop_shot")
    assert fake.sent(b"MC\x03")
    assert fake.shot is False

    # Idle stop must not press OK (that opens the device MENU).
    fake._ticks = 0
    await wait_for_frames(hass, fake, fake.frames_sent + 3)
    assert state_of(hass, entry, "ito_leva_state").state == "IDLE"
    before_ok = fake.commands.count(b"MC\x03")
    await press(hass, entry, "ito_leva_stop_shot")
    assert fake.commands.count(b"MC\x03") == before_ok


async def test_start_from_standby_wakes_first(hass, entry, fake):
    await wait_for_frames(hass, fake)
    await press(hass, entry, "ito_leva_standby")
    await wait_for_frames(hass, fake, fake.frames_sent + 5)
    assert fake.sent(b"MCE")
    assert fake.awake is False
    assert state_of(hass, entry, "ito_leva_state").state == "STANDBY"

    await press(hass, entry, "ito_leva_start_shot")
    assert fake.sent(b"MCa")
    assert fake.sent(b"MCcDOSE")
    assert fake.shot is True
    await wait_for_frames(hass, fake, fake.frames_sent + 5)
    assert state_of(hass, entry, "ito_leva_state").state == "PULLING"


async def test_start_watchdog_notification(hass, entry, fake):
    """If no PULLING appears within 3 s, a persistent notification is created."""
    fake.block_shot = True
    await wait_for_frames(hass, fake)
    notifications = async_mock_service(hass, "persistent_notification", "create")

    await press(hass, entry, "ito_leva_start_shot")

    assert fake.sent(b"MCcDOSE")
    assert len(notifications) == 1
    data = notifications[0].data
    assert data["notification_id"] == "ito_leva_start_failed"
    assert "Kein Shot-Start" in data["message"]


async def test_alarm_token_updates_sensor_and_clears(hass, entry, fake):
    await wait_for_frames(hass, fake)
    notifications = async_mock_service(hass, "persistent_notification", "create")

    await fake.alarm("[BACKFL]")
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()

    assert state_of(hass, entry, "ito_leva_alarm").state == "[BACKFL]"
    assert len(notifications) == 1
    data = notifications[0].data
    assert data["title"] == "ITO Leva! [BACKFL]"
    assert "Backflush" in data["message"]

    # A silent alarm ages out instead of staying forever.
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._alarm_ts -= 61
    await wait_for_frames(hass, fake, fake.frames_sent + 3)
    assert state_of(hass, entry, "ito_leva_alarm").state == "OK"


async def test_mcu_refresh_setup_service(hass, entry, fake):
    await wait_for_frames(hass, fake)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    await hass.services.async_call(DOMAIN, "refresh_setup", blocking=True)
    await hass.async_block_till_done()

    tree = coordinator.setup_tree
    assert tree["PID 1"]["Control"] == "PID"
    assert tree["PID 1"]["Setpoint"] == "94.2"
    assert tree["Sensors"]["Temperature"]["Type"] == "NTC"
    # Telemetry frames captured during the MCu window are not tree keys.
    assert "{" not in tree

    assert state_of(hass, entry, "ito_leva_setup_pid_control").state == "PID"
    assert float(
        state_of(hass, entry, "ito_leva_setup_pid_setpoint").state
    ) == pytest.approx(94.2)
    assert state_of(hass, entry, "ito_leva_setup_temp_type").state == "NTC"


async def test_lcd_mirror_persists_and_state_priority(hass, entry, fake):
    await wait_for_frames(hass, fake)
    mirror = entity_id_for(hass, entry, "ito_leva_lcd")

    # Rich-only mode: no LCD rows yet.
    assert hass.states.get(mirror).state == "unknown"

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.client.send_command("MC@\r")  # switch device to LCD mode
    await wait_for_frames(hass, fake, fake.frames_sent + 10)
    state = hass.states.get(mirror).state
    assert "WARM-UP" in state
    assert "93.5" in state
    assert state_of(hass, entry, "ito_leva_state").state == "HEATING"

    # Back to rich mode: rows persist and HEATING briefly wins over rich IDLE.
    coordinator.client.send_command("MCr\r")
    await wait_for_frames(hass, fake, fake.frames_sent + 5)
    assert "WARM-UP" in hass.states.get(mirror).state
    assert state_of(hass, entry, "ito_leva_state").state == "HEATING"

    # Once the LCD-only state is no longer fresh, rich IDLE is authoritative.
    await asyncio.sleep(3.2)
    await hass.async_block_till_done()
    assert state_of(hass, entry, "ito_leva_state").state == "IDLE"
    assert "WARM-UP" in hass.states.get(mirror).state


async def test_power_buttons_use_short_commands(hass, entry, fake):
    await wait_for_frames(hass, fake)

    standby_id = entity_id_for(hass, entry, "ito_leva_standby")
    assert hass.states.get(standby_id) is not None, standby_id
    await press(hass, entry, "ito_leva_standby")
    assert fake.sent(b"MCE"), fake.commands
    assert not fake.sent(b"MCcSTANDBY")
    await wait_for_frames(hass, fake, fake.frames_sent + 5)
    assert fake.awake is False
    assert state_of(hass, entry, "ito_leva_state").state == "STANDBY"

    await press(hass, entry, "ito_leva_wake")
    assert fake.sent(b"MCa")
    await wait_for_frames(hass, fake, fake.frames_sent + 5)
    assert fake.awake is True
    assert state_of(hass, entry, "ito_leva_state").state == "IDLE"

    await press(hass, entry, "ito_leva_eco")
    assert fake.sent(b"MCe")
    await wait_for_frames(hass, fake, fake.frames_sent + 5)
    assert fake.awake is True
    assert float(state_of(hass, entry, "ito_leva_setpoint").state) == pytest.approx(81.3)


async def test_migrates_legacy_host_based_entity_ids(hass, fake):
    """A v1 entry keeps working and its registry IDs are rewritten."""
    registry = er.async_get(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "127.0.0.1", "port": fake.port},
        unique_id=f"127.0.0.1:{fake.port}",
        version=1,
    )
    entry.add_to_hass(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "ito_leva_temp",
        config_entry=entry,
        suggested_object_id="ito_leva_127_0_0_1_5",
    )
    registry.async_get_or_create(
        "number",
        DOMAIN,
        "ito_leva_setpoint_number",
        config_entry=entry,
        suggested_object_id="ito_leva_127_0_0_1_9",
    )
    registry.async_get_or_create(
        "text",
        DOMAIN,
        "ito_leva_lcd",
        config_entry=entry,
        suggested_object_id="ito_leva_127_0_0_1_12",
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entity_id_for(hass, entry, "ito_leva_temp") == "sensor.ito_leva_temp"
    assert entity_id_for(hass, entry, "ito_leva_setpoint_number") == (
        "number.ito_leva_setpoint"
    )
    assert entity_id_for(hass, entry, "ito_leva_lcd") == (
        "text.ito_leva_lcd_mirror"
    )
    temp_state = hass.states.get("sensor.ito_leva_temp")
    assert temp_state is not None
    assert temp_state.attributes["friendly_name"] == (
        "ITO Leva! (127.0.0.1) Group temperature"
    )

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_unreachable_host_is_rejected_by_config_flow(hass):
    fake = FakeIto()
    await fake.start()
    port = fake.port
    await fake.stop()  # nothing listens on that port any more

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": port}
    )
    await hass.async_block_till_done()

    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_unreachable_entry_raises_not_ready(hass):
    fake = FakeIto()
    await fake.start()
    port = fake.port
    await fake.stop()

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "127.0.0.1", "port": port},
        unique_id=f"127.0.0.1:{port}",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is False
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_services_survive_unloading_one_of_two_entries(hass, fake):
    """Services are global; they disappear only with the last entry."""
    first = await setup_entry(hass, fake)
    await wait_for_frames(hass, fake)

    second_fake = FakeIto()
    await second_fake.start()
    try:
        second = MockConfigEntry(
            domain=DOMAIN,
            data={"host": "127.0.0.1", "port": second_fake.port},
            unique_id=f"127.0.0.1:{second_fake.port}",
        )
        second.add_to_hass(hass)
        assert await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

        assert first.state is ConfigEntryState.LOADED
        assert second.state is ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, "start_shot")

        assert await hass.config_entries.async_unload(first.entry_id)
        await hass.async_block_till_done()

        assert second.state is ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, "start_shot")
        assert hass.services.has_service(DOMAIN, "refresh_setup")

        await hass.config_entries.async_unload(second.entry_id)
        await hass.async_block_till_done()
        assert not hass.services.has_service(DOMAIN, "start_shot")
        assert not hass.services.has_service(DOMAIN, "refresh_setup")
    finally:
        await second_fake.stop()


async def test_unload_closes_connection(hass, fake):
    entry = await setup_entry(hass, fake)
    await wait_for_frames(hass, fake)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.client._writer is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert entry.entry_id not in hass.data[DOMAIN]
    assert coordinator.client._writer is None
    assert not hass.services.has_service(DOMAIN, "start_shot")
