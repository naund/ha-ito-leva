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
        5: "OFF",
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
    sample = parse_rich_frame("{" + "?" * 290 + "}")
    assert sample is not None
    assert sample["pressure"] is None
    assert sample["temp"] is None
    assert sample["state"] == "IDLE"


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
