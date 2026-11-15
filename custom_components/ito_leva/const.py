"""Shared constants for the ITO Leva! integration (Phase 1).

Detailed comments on purpose: every magic number here comes from the
reference implementation (proxy.js) or the Ranchilio.md handoff spec,
so a future agent can trace each value back to its source.
"""

DOMAIN = "ito_leva"

# Connection defaults. The live machine in this household is fixed at
# 192.168.178.21 (see Ranchilio.md); the config flow still allows override
# for other networks / AP mode (192.168.4.1).
DEFAULT_HOST = "192.168.178.21"
DEFAULT_PORT = 23

# MC protocol handshake lines (proxy.js TCP layer).
CMD_PROBE = "\nMC?\r"  # initial probe after TCP connect (optional but harmless)
CMD_RICH = "MCr\r"  # rich-mode telemetry: 10 Hz `{ ... }` + `<...>` LCD frames
CMD_LCD_ONLY = "MC@\r"  # LCD-only mode, Phase 2 (menu navigation) only
CMD_WAKE = "MCa\r"  # wake from standby / ECO
CMD_SETPOINT_UP = "MC+\r"  # +0.1 C
CMD_SETPOINT_DOWN = "MC-\r"  # -0.1 C
CMD_DUMP_SETUP = "MCu\r"  # full setup dump, ~3-5 s of streamed lines

# Power state. Verified live: the firmware ignores the long MCc forms; the
# short single-letter commands MCE / MCe actually enter standby / eco.
CMD_ECO = "MCe\r"
CMD_STANDBY = "MCE\r"

# Assignable single-shot functions (proxy.js MCC_MAP subset used in Phase 1).
CMD_FLUSH = "MCcFLUSH\r"
CMD_WARMUP = "MCcWARMUP\r"
CMD_PUMP_PB = "MCcPUMP PB\r"  # toggle push-button pump (start/stop)
CMD_DOSE = "MCcDOSE\r"  # fire active dose (= Start shot in Phase 1)

# Virtual button bytes, sent as `MC<byte>\r` + `MC\x00\r` release after 30 ms.
# UP/DOWN/CANCEL are Phase 2 (menu navigation); OK doubles as Stop shot.
BTN_UP = "\x01"
BTN_DOWN = "\x02"
BTN_OK = "\x03"  # also: stop a running shot, dismiss WAIT
BTN_CANCEL = "\x04"
BTN_SHORTCUT_1 = "\x05"  # start shot from home screen
BTN_SHORTCUT_2 = "\x06"
BTN_RELEASE = "\x00"
PRESS_RELEASE_DELAY = 0.03  # 30 ms between press and release (proxy.js mcPress)

# Timing (proxy.js CONFIG + startShot()).
KEEPALIVE_INTERVAL = 7.0  # bare `\n` every 7 s keeps the ~10 s idle timeout away
RECONNECT_DELAY = 3.0  # s between reconnect attempts
SOCKET_TIMEOUT = 15.0  # socket timeout in s
SHOT_WATCHDOG_TIMEOUT = 3.0  # s: Start -> PULLING/BREWING, else shot error
MCU_CAPTURE_TIMEOUT = 5.0  # s: MCu dump collection window (dump takes ~3-4 s)
START_WAKE_RETRIES = 4  # MCa attempts from STANDBY
START_WAKE_WAIT = 0.6  # s between wake attempts (STANDBY->IDLE is slow)
START_MENU_EXITS = 4  # CANCEL presses to leave MENU
START_STEP_WAIT = 0.15  # s between menu-exit steps

# Setup phase uses its own timeout so an unreachable host fails fast into
# ConfigEntryNotReady instead of leaving a "loaded" entry with no data.
CONNECT_TIMEOUT = 10.0  # s to TCP connect + rich handshake

# Shot control guard: OK only makes sense while a program is running (or a
# frozen WAIT result is on screen). In IDLE/MENU it would open the menu.
STOP_STATES = ("PULLING", "BREWING", "FLUSHING", "WAIT")

# LCD-only states (HEATING/STANDBY/MENU/...) are kept for this long when a
# rich frame reports plain IDLE, so the richer LCD screen wins briefly.
LCD_STATE_HOLD = 3.0  # s
# An alarm token is cleared when no new token arrived for this long.
ALARM_CLEAR_TIMEOUT = 60.0  # s

# Telemetry throttle: raw frames arrive at 10 Hz; HA states update at most
# this often. The full-rate buffer stays in memory for the Phase 2 chart.
TELEMETRY_THROTTLE = 1.0  # s between HA state pushes (1 Hz)
RING_BUFFER_MAXLEN = 600  # 60 s at 10 Hz, in-memory only (Phase 2 chart source)

# LCD rendering: firmware bytes mapped for the text mirror entity.
CURSOR_GLYPH = "\u25ba"  # \x04 cursor -> BLACK RIGHT-POINTING POINTER
FILL_GLYPH = "\u2713"  # \x05 progress fill -> CHECK MARK
DEGREE_BYTE = "\xdf"  # latin-1 degree sign byte used by the firmware

# Platforms exposed in Phase 1.
PLATFORMS = ("sensor", "button", "number", "text")

# Stable entity IDs used by dashboards/automations. Explicit IDs are needed
# because HA would otherwise derive them from the device name (host-based).
# The mapping is also used to migrate registries created by version 1.
ENTITY_IDS: dict[str, str] = {
    "state": "sensor.ito_leva_state",
    "pressure": "sensor.ito_leva_pressure",
    "flow_weight": "sensor.ito_leva_flow_weight",
    "flow_meter": "sensor.ito_leva_flow_meter",
    "temp": "sensor.ito_leva_temp",
    "setpoint": "sensor.ito_leva_setpoint",
    "weight": "sensor.ito_leva_weight",
    "volume": "sensor.ito_leva_volume",
    "timer": "sensor.ito_leva_timer",
    "alarm": "sensor.ito_leva_alarm",
    "connection": "sensor.ito_leva_connection",
    "setup_pid_control": "sensor.ito_leva_setup_pid_control",
    "setup_pid_setpoint": "sensor.ito_leva_setup_pid_setpoint",
    "setup_temp_type": "sensor.ito_leva_setup_temp_type",
    "setpoint_number": "number.ito_leva_setpoint",
    "start_shot": "button.ito_leva_start_shot",
    "stop_shot": "button.ito_leva_stop_shot",
    "flush": "button.ito_leva_flush",
    "warmup": "button.ito_leva_warmup",
    "pump_pb": "button.ito_leva_pump_pb",
    "backflush": "button.ito_leva_backflush",
    "descale": "button.ito_leva_descale",
    "wake": "button.ito_leva_wake",
    "standby": "button.ito_leva_standby",
    "eco": "button.ito_leva_eco",
    "dose": "button.ito_leva_dose",
    "lcd": "text.ito_leva_lcd_mirror",
    "active_profile": "text.ito_leva_active_profile",
}
