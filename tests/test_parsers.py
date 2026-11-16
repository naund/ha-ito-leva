"""Offline parser tests (no HA runtime needed): python3 -m pytest tests/ -q.

Frames below follow the verified proxy.js layout (see parsers.py header).
"""

import sys
import types
from pathlib import Path

COMPONENTS = Path(__file__).resolve().parents[1] / "custom_components"
# Register a bare `ito_leva` package WITHOUT executing its HA-coupled
# __init__.py, so parsers/models import standalone (relative imports keep
# working through the package __path__).
_pkg = types.ModuleType("ito_leva")
_pkg.__path__ = [str(COMPONENTS / "ito_leva")]
sys.modules["ito_leva"] = _pkg

from ito_leva.parsers import (
    ALARM_REASONS,
    classify_lcd_state,
    derive_status,
    parse_alarm_token,
    parse_lcd_frame,
    parse_mcu_tree,
    parse_rich_frame,
    render_lcd_row,
)


def _rich_body(overrides: dict[int, str] | None = None) -> str:
    body = list("?" * 290)
    fields = {
        3: "*",
        5: "PID",
        14: "094.2",
        153: "042",
        156: "02622",
        175: "093.50",
        198: "09.00",
        203: "08.80",
        208: "09.20",
        227: "107.5",
        232: "02.46",
        237: "Adpt(form",
        273: "Declining",
    }
    fields.update(overrides or {})
    for pos, text in fields.items():
        body[pos - 2 : pos - 2 + len(text)] = list(text)
    return "".join(body)


def test_rich_pulling_frame():
    sample = parse_rich_frame("{" + _rich_body() + "}")
    assert sample is not None
    assert sample["state"] == "PULLING"
    assert sample["shot_running"] is True
    assert sample["pressure"] == 9.0
    assert sample["pressure_low"] == 8.8
    assert sample["pressure_high"] == 9.2
    assert sample["temp"] == 93.5
    assert sample["volume"] == 42.0
    assert sample["timer"] == 26.0
    assert sample["timer_frac"] == 26.22
    assert sample["ticks"] == 2622
    assert sample["weight"] == 107.5
    assert sample["flow_ml"] == 2.46
    assert sample["active_profile_name"] == "Declining"
    assert sample["setpoint"] == 94.2
    assert sample["pid_on"] is True
    assert sample["pid_control"] == "PID"


def test_rich_pressure_controller_fields():
    """Pump PI internals: output = bias + P + I (manual p.109)."""
    sample = parse_rich_frame(
        "{"
        + _rich_body(
            {
                19: "085",  # Heat PID %
                193: "09.00",  # Pressure SP
                213: "070",  # Phase
                216: "052",  # Phase (bias)
                219: "0012",  # Phase (P), 4 chars
                223: "0006",  # Phase (I), 4 chars
            }
        )
        + "}"
    )
    assert sample is not None
    assert sample["pid_output"] == 85
    assert sample["pressure_sp"] == 9.0
    assert sample["phase"] == 70.0
    assert sample["phase_bias"] == 52.0
    assert sample["phase_p"] == 12.0
    assert sample["phase_i"] == 6.0
    assert sample["phase"] == sample["phase_bias"] + sample["phase_p"] + sample["phase_i"]


def test_rich_pressure_fields_absent_when_question_filled():
    sample = parse_rich_frame("{" + _rich_body() + "}")
    assert sample is not None
    assert sample["phase"] is None
    assert sample["pressure_sp"] is None


def test_rich_idle_frame():
    sample = parse_rich_frame("{" + _rich_body({3: "-", 156: "00000"}) + "}")
    assert sample is not None
    assert sample["state"] == "IDLE"
    assert sample["shot_running"] is False
    assert sample["timer"] == 0.0


def test_rich_wait_frame():
    # Leftover timer without '*' = frozen post-shot result (WAIT).
    sample = parse_rich_frame("{" + _rich_body({3: "-"}) + "}")
    assert sample is not None
    assert sample["state"] == "WAIT"


def test_rich_unavailable_fields_are_none():
    # A fully '?' frame is what the firmware sends in STANDBY.
    sample = parse_rich_frame("{" + "?" * 290 + "}")
    assert sample is not None
    assert sample["pressure"] is None
    assert sample["temp"] is None
    assert sample["setpoint"] is None
    assert sample["state"] == "STANDBY"


def test_rich_pid_off_and_setpoint_validation():
    sample = parse_rich_frame(
        "{" + _rich_body({3: "-", 5: "OFF", 14: "xxx.x"}) + "}"
    )
    assert sample is not None
    assert sample["pid_on"] is False
    assert sample["setpoint"] is None


def test_lcd_glyph_rendering():
    assert render_lcd_row("a\x04b\x05c\xdfd") == "a\u25bab\u2713c\u00b0d"


def test_lcd_home_frame():
    row1 = "Declining       "  # 16 chars
    row2 = " 1: OFF         "
    row3 = " 9.0b 93.5\xdf 23s "
    row4 = "    ml          "
    assert len(row3) == 16
    frame = "<102" + row1 + row2 + row3 + row4 + ">"
    sample = parse_lcd_frame(frame)
    assert sample is not None
    assert sample["prefix"] == "102"
    assert sample["active_profile_name"] == "Declining"
    assert sample["pressure"] == 9.0
    assert sample["temp"] == 93.5
    assert sample["timer"] == 23.0


def test_lcd_temp_without_decimals():
    row3 = " 85\xdf  0.0b   0s "
    assert len(row3) == 16
    frame = "<102" + "LEVER F".ljust(16) + "1: 85\xdf".ljust(16) + row3 + " 0.0b-  0s   0ml" + ">"
    sample = parse_lcd_frame(frame)
    assert sample is not None
    assert sample["temp"] == 85.0
    assert sample["pressure"] == 0.0


def test_lcd_state_rules():
    assert classify_lcd_state("002   STANDBY      ") == "STANDBY"
    assert classify_lcd_state("101MENU  Info       ") == "MENU"
    assert classify_lcd_state("102 ... R=2.1 ...   ") == "PULLING"
    assert classify_lcd_state("102 ... Q=55 ...    ") == "PULLING"
    assert classify_lcd_state("102 ... SCALE ...   ") == "WAIT"
    assert classify_lcd_state("102 ... 11s  62ml  ") == "WAIT"


def test_alarm_tokens():
    assert parse_alarm_token("Title [REFILL] stuff") == "[REFILL]"
    assert parse_alarm_token("[DSCALE]") == "[DSCALE]"
    assert parse_alarm_token("[NO SNS] zero cross") == "[NO SNS]"
    assert parse_alarm_token("plain telemetry line") is None
    # Terminology guard: Waage vs. Kalk must stay distinct.
    assert "Waage" in ALARM_REASONS["[SCALE]"]
    assert "Entkalken" in ALARM_REASONS["[DSCALE]"]


def test_mcu_tree_depth_parsing():
    lines = [
        "PID 1...",
        "| Control     PID",
        "| Setpoint    93.0",
        "Sensors...",
        "| Temperature...",
        "| | Type   NTC",
    ]
    tree = parse_mcu_tree(lines)
    assert tree["PID 1"]["Control"] == "PID"
    assert tree["PID 1"]["Setpoint"] == "93.0"
    assert tree["Sensors"]["Temperature"]["Type"] == "NTC"


def test_derive_status():
    """Status is derived from setpoint/temperature in rich mode."""
    base = {"temp": 93.5, "setpoint": 94.0, "eco_setpoint": 82.3}
    assert derive_status(connected=True, state="IDLE", **base) == "READY"
    # Within the firmware's +/-1.0 K ready window (manual p.196).
    assert derive_status(connected=True, state="IDLE", temp=93.1, setpoint=94.0) == (
        "READY"
    )
    assert derive_status(connected=True, state="IDLE", temp=92.5, setpoint=94.0) == (
        "HEATING"
    )
    # Above target by more than the tolerance = easing down, not ready.
    assert derive_status(connected=True, state="IDLE", temp=98.0, setpoint=94.0) == (
        "COOLING"
    )
    # The warm-up program overrides the setpoint: a normal phase, not an error.
    assert derive_status(
        connected=True, state="IDLE", temp=47.4, setpoint=110.0, override="WARM-UP"
    ) == "WARM-UP"
    # Steam: PID bypassed, boiler past the brew setpoint at high output.
    assert derive_status(
        connected=True, state="IDLE", temp=120.0, setpoint=94.0, pid_output=100
    ) == "STEAM"
    # Steam wins over eco (steaming from eco), cooling does not.
    assert derive_status(
        connected=True, state="IDLE", temp=120.0, setpoint=81.3,
        eco_setpoint=82.3, pid_output=100,
    ) == "STEAM"
    assert derive_status(
        connected=True, state="IDLE", temp=98.0, setpoint=94.0, pid_output=0
    ) == "COOLING"
    # Warm-up override: heating towards the program's own setpoint.
    assert derive_status(connected=True, state="IDLE", temp=47.4, setpoint=110.0) == (
        "HEATING"
    )
    # Eco sits on its own setpoint (live 81.3 vs stored 82.3 on the reference).
    assert derive_status(connected=True, state="IDLE", temp=81.0, setpoint=81.3,
                         eco_setpoint=82.3) == "ECO"
    assert derive_status(connected=True, state="STANDBY", temp=None,
                         setpoint=None) == "STANDBY"
    assert derive_status(connected=True, state="PULLING", **base) == "BREWING"
    assert derive_status(connected=True, state="WAIT", **base) == "WAIT"
    # No connection / no frames yet must never look like READY.
    assert derive_status(connected=False, state="IDLE", **base) == "OFFLINE"
    assert derive_status(connected=True, state="UNKNOWN", **base) == "OFFLINE"


def test_mcu_tree_skips_telemetry_frames():
    tree = parse_mcu_tree(
        [
            "PID 1...",
            "| Control     PID",
            "{" + "?" * 20 + "}",
            "<102LCD ROW>",
        ]
    )
    assert tree == {"PID 1": {"Control": "PID"}}
