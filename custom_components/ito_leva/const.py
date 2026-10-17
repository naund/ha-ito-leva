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

# Assignable single-shot functions (proxy.js MCC_MAP subset used in Phase 1).
CMD_ECO = "MCcECO\r"
CMD_STANDBY = "MCcSTANDBY\r"
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
START_WAKE_RETRIES = 3  # MCa attempts from STANDBY
START_WAKE_WAIT = 0.6  # s between wake attempts (STANDBY->IDLE is slow)
START_MENU_EXITS = 4  # CANCEL presses to leave MENU
START_STEP_WAIT = 0.15  # s between menu-exit steps

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
