"""Pure MC-protocol parsers (no HA imports, unit-testable).

Direct port of the reference implementation's parsing rules
(`proxy.js`: `parseRichFrame`, `parseLevaDisplay`, nav-display helpers).
Positions are 1-indexed *frame* positions; the body inside `{ ... }` is
0-indexed, so body offset = frame position - 2.

Frame layout (verified live 2026-04, see proxy.js header comment):
  Pos  3        profile marker: '-' idle, '*' shot running, letter = profile
  Pos  5-7      PID 1 status ('OFF' / 'ON ')
  Pos 153-155  volume, ml (3 digits, flow meter)
  Pos 156-160  ticks, 5-digit 100 Hz shot counter (timer s = ticks // 100)
  Pos 175-180  boiler temp, C ("NNN.NN")
  Pos 198-202  pressure, bar ("NN.NN")
  Pos 203-207  pressure band low, Pos 208-212 band high
  Pos 227-231  weight, g ("NNN.N", scale/Waage)
  Pos 232-236  flow, ml/s ("NN.NN", flow meter, = LCD `R=` field)
  Pos 237-281  profile slot names 1-4 + active profile name
Unavailable fields are '?' filled and parse as None.
"""

from __future__ import annotations

import re
from typing import Optional

from .const import CURSOR_GLYPH, FILL_GLYPH

# LCD glyph mapping for the text mirror (proxy.js cleanLine()).
_LCD_GLYPH_MAP = {
    "\x04": CURSOR_GLYPH,  # cursor
    "\x05": FILL_GLYPH,  # progress-bar fill
    "\xdf": "\u00b0",  # firmware degree byte -> °
}


def render_lcd_row(raw: str) -> str:
    """Map firmware control bytes to display glyphs, drop the rest."""
    out: list[str] = []
    for char in raw:
        if char in _LCD_GLYPH_MAP:
            out.append(_LCD_GLYPH_MAP[char])
        elif 32 <= ord(char) <= 126:
            out.append(char)
        else:
            out.append(" ")
    return "".join(out)


def _rich_field(body: str, frame_pos: int, length: int) -> Optional[str]:
    """Read a fixed-width field; None when out of range or '?' filled."""
    offset = frame_pos - 2
    if offset < 0 or offset + length > len(body):
        return None
    chunk = body[offset : offset + length]
    return None if "?" in chunk else chunk


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value.strip())
    except (ValueError, TypeError):
        return None
    return number if number == number else None  # filter NaN


def parse_rich_frame(line: str) -> Optional[dict]:
    """Parse a `{ ... }` rich telemetry frame. Returns None if unparseable."""
    line = line.strip()
    if not (line.startswith("{") and line.endswith("}")):
        return None
    body = line[1:-1]

    marker = _rich_field(body, 3, 1)
    shot_running = marker == "*"
    active_profile: Optional[str] = None
    if marker and marker not in ("-", "*"):
        active_profile = marker

    pid_status = _rich_field(body, 5, 3)
    pid_on = pid_status.strip() == "ON" if pid_status else None

    volume_raw = _rich_field(body, 153, 3)
    volume = int(volume_raw) if volume_raw and volume_raw.strip().isdigit() else None

    ticks_raw = _rich_field(body, 156, 5)
    ticks = int(ticks_raw) if ticks_raw and ticks_raw.strip().isdigit() else None
    timer = ticks // 100 if ticks is not None else None
    timer_frac = ticks / 100.0 if ticks is not None else None

    temp = _parse_float(_rich_field(body, 175, 6))
    if temp is not None and not 0 < temp < 200:
        temp = None
    pressure = _parse_float(_rich_field(body, 198, 5))
    if pressure is not None and pressure < 0:
        pressure = None
    pressure_low = _parse_float(_rich_field(body, 203, 5))
    pressure_high = _parse_float(_rich_field(body, 208, 5))
    weight = _parse_float(_rich_field(body, 227, 5))
    flow_ml = _parse_float(_rich_field(body, 232, 5))

    slot_names = [
        (_rich_field(body, 237, 9) or "").strip() or None,
        (_rich_field(body, 250, 5) or "").strip() or None,
        (_rich_field(body, 260, 4) or "").strip() or None,
        (_rich_field(body, 264, 9) or "").strip() or None,
    ]
    active_profile_name = (_rich_field(body, 273, 9) or "").strip() or None

    # State inference (proxy.js): '*' is definitive PULLING; a leftover
    # timer without '*' is the frozen post-shot result (WAIT); else IDLE.
    if shot_running:
        state = "PULLING"
    elif timer is not None and timer > 0:
        state = "WAIT"
    else:
        state = "IDLE"

    return {
        "state": state,
        "pressure": pressure,
        "pressure_low": pressure_low,
        "pressure_high": pressure_high,
        "temp": temp,
        "volume": float(volume) if volume is not None else None,
        "timer": float(timer) if timer is not None else None,
        "timer_frac": timer_frac,
        "ticks": ticks,
        "weight": weight,
        "flow_ml": flow_ml if flow_ml is not None else 0.0,
        "shot_running": shot_running,
        "active_profile": active_profile,
        "active_profile_name": active_profile_name,
        "profile_slot_names": slot_names,
        "pid_on": pid_on,
        "rich": True,
    }


# State keywords for LCD frames, in proxy.js priority order. PULLING markers
# (R=/Q=) win over WAIT markers because SCALE also appears mid-cycle.
_LCD_PULLING_RE = re.compile(r"R\s*=|Q\s*=")
_LCD_TIMER_ML_RE = re.compile(r"[1-9]\d*s\s+\d+(?:\.\d+)?ml\s*$")
_LCD_TIMER_G_RE = re.compile(r"[1-9]\d*s\s+\d+\.\d+g\s*$")
_LCD_PRESSURE_RE = re.compile(r"(\d+\.?\d*)b")
_LCD_TEMP_DEG_RE = re.compile(r"(\d+\.\d+)\s*\xdf")
_LCD_TEMP_ASCII_RE = re.compile(r"b\s+(\d+\.\d+)\s+\d+\s*s")
_LCD_VOLUME_RE = re.compile(r"\s(\d+)\s*ml(?:\s*>)?\s*$")
_LCD_TIMER_RE = re.compile(r"(?:^|\s)(\d+)s(?=\s|>|$)")
_LCD_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*g\b")
_LCD_FLOW_RE = re.compile(r"R\s*=\s*(\d+(?:\.\d+)?)")


def classify_lcd_state(inner: str) -> str:
    """Classify machine state from LCD inner text (proxy.js rule order)."""
    upper = inner.upper()
    if "STANDBY" in upper:
        return "STANDBY"
    if "MENU" in upper or re.match(r"^1[05]1", inner):
        return "MENU"
    if _LCD_PULLING_RE.search(upper):
        return "PULLING"
    if "[NEXT]" in upper or "SCALE" in upper or _LCD_TIMER_G_RE.search(inner):
        return "WAIT"
    if "PULLING" in upper or "BREW" in upper or "DOSE" in upper:
        return "PULLING"
    if "STEAM" in upper:
        return "STEAMING"
    if "HEAT" in upper or "WARM" in upper:
        return "HEATING"
    if "FLUSH" in upper:
        return "FLUSHING"
    if "ECO" in upper:
        return "STANDBY"
    if _LCD_TIMER_ML_RE.search(inner):
        return "WAIT"
    return "IDLE"


def parse_lcd_frame(raw: str) -> Optional[dict]:
    """Parse a `<NNN + 4x16 chars>` LCD frame (raw, control chars kept).

    Returns prefix, four rendered rows, active profile name (row 1) and the
    best-effort telemetry subset. Temperature needs the raw degree byte;
    on ASCII-filtered input the fallback regex may miss it (returns None).
    """
    start = raw.find("<")
    end = raw.rfind(">")
    if start < 0 or end <= start:
        return None
    inner = raw[start + 1 : end]
    if len(inner) < 67:  # 3-char prefix + 4x16 rows
        return None
    prefix = inner[0:3]
    rows = [render_lcd_row(inner[3 + i * 16 : 3 + (i + 1) * 16]) for i in range(4)]

    pressure_m = _LCD_PRESSURE_RE.search(inner)
    pressure = float(pressure_m.group(1)) if pressure_m else None

    temp: Optional[float] = None
    temp_m = _LCD_TEMP_DEG_RE.search(raw)
    if temp_m:
        candidate = float(temp_m.group(1))
        if 0 < candidate < 200:
            temp = candidate
    if temp is None:
        fallback_m = _LCD_TEMP_ASCII_RE.search(inner)
        if fallback_m:
            candidate = float(fallback_m.group(1))
            if 0 < candidate < 200:
                temp = candidate

    volume_m = _LCD_VOLUME_RE.search(inner)
    volume = float(volume_m.group(1)) if volume_m else None

    timers = _LCD_TIMER_RE.findall(inner)
    timer = float(timers[-1]) if timers else None

    weight_m = _LCD_WEIGHT_RE.search(inner)
    weight = float(weight_m.group(1)) if weight_m else None

    flow_m = _LCD_FLOW_RE.search(inner.upper())
    flow_ml = float(flow_m.group(1)) if flow_m else 0.0

    return {
        "prefix": prefix,
        "rows": rows,
        "active_profile_name": rows[0].strip() or None,
        "state": classify_lcd_state(inner),
        "pressure": pressure,
        "temp": temp,
        "volume": volume,
        "timer": timer,
        "weight": weight,
        "flow_ml": flow_ml,
    }


# Alarm tokens: firmware refusal markers overprinted on the LCD title and
# echoed as a one-line `[TOKEN]` on the wire (proxy.js ALARM_REASONS subset
# from the handoff spec). DE hints separate Waage (scale) from Kalk (scale).
ALARM_REASONS: dict[str, str] = {
    "[OFF]": "Picked PID has Control = OFF",
    "[SCALE]": "Weighing scale (DE: Waage) not connected",
    "[FLWMTR]": "Flow meter not installed",
    "[NO SNS]": "No AC zero-cross at SNS clamp",
    "[NO_SNS]": "No AC zero-cross at SNS clamp",
    "[GAUGE]": "Pressure-sensor error",
    "[BLE_OFF]": "Bluetooth radio off",
    "[VALVE]": "3-way solenoid error",
    "[PROTECT]": "Pump or boiler protection trip",
    "[REFILL]": "Tank empty",
    "[FILTER]": "Filter exchange due",
    "[DSCALE]": "Descale required (DE: Entkalken — limescale/Kalk entfernen)",
    "[BACKFL]": "Backflush required",
    "[SETUP]": "Setup incomplete",
}

_ALARM_RE = re.compile(r"\[[A-Z_]+\]")  # e.g. [REFILL]; [NO SNS] has a space


def parse_alarm_token(line: str) -> Optional[str]:
    """Return the alarm token in a wire line, or None.

    Handles the spaced `[NO SNS]` variant explicitly; otherwise matches
    the bracketed token and validates it against ALARM_REASONS.
    """
    if "[NO SNS]" in line:
        return "[NO SNS]"
    match = _ALARM_RE.search(line)
    if match and match.group(0) in ALARM_REASONS:
        return match.group(0)
    return None


def parse_mcu_tree(lines: list[str]) -> dict:
    """Parse an MCu dump into a nested dict by leading-pipe depth.

    Each `|` level adds one dict level; `Label value` leaves become
    `node["Label"] = "value"`, bare `Label...` headers become sub-dicts.
    This is the Phase 1 generic reader — the full SETTINGS_SECTIONS schema
    mapping is Phase 3 scope.
    """
    root: dict = {}
    stack: list[dict] = [root]
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("<"):
            continue
        depth = 0
        rest = raw
        while True:
            pipe = rest.find("|")
            if pipe < 0:
                break
            depth += 1
            rest = rest[pipe + 1 :]
        label = rest.strip()
        if not label:
            continue
        while len(stack) > depth + 1:
            stack.pop()
        parent = stack[-1]
        if label.endswith("..."):
            node: dict = {}
            parent[label[:-3].strip()] = node
            stack.append(node)
        else:
            parts = label.split(None, 1)
            if len(parts) == 2:
                parent[parts[0]] = parts[1]
            else:
                parent[parts[0]] = ""
    return root
