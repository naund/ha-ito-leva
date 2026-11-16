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
CMD_RICH = "MCr\r"  # rich telemetry only (~4 Hz `{ ... }`; no LCD frames)
CMD_LCD_ONLY = "MC@\r"  # display stream only (~10 Hz `<...>`; no rich frames)
CMD_WAKE = "MCa\r"  # wake from standby / ECO
# Setpoint keys. The size of one press is NOT fixed: `MC#` sets the increment
# to 0.1, 1.0 or 10 K (firmware manual, Appendix: Serial menu, p.227), and the
# vendor status-monitor frame exposes the active value as `at=163` ("increment",
# live: 001.0). Never assume 0.1 - measure the step from the read-back.
CMD_SETPOINT_UP = "MC+\r"
CMD_SETPOINT_DOWN = "MC-\r"
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
MCU_QUIET_TIMEOUT = 1.5  # s without dump lines = dump finished (early exit)
SETUP_INITIAL_DELAY = 3.0  # s after startup before the first automatic MCu read
START_WAKE_RETRIES = 4  # MCa attempts from STANDBY
START_WAKE_WAIT = 0.6  # s between wake attempts (STANDBY->IDLE is slow)
START_MENU_EXITS = 4  # CANCEL presses to leave MENU
START_STEP_WAIT = 0.15  # s between menu-exit steps

# Setup phase uses its own timeout so an unreachable host fails fast into
# ConfigEntryNotReady instead of leaving a "loaded" entry with no data.
CONNECT_TIMEOUT = 10.0  # s to TCP connect + rich handshake

# Shot control guard: OK only makes sense while a program is actually running.
# WAIT is deliberately NOT included: the firmware keeps the last shot's ticks
# in the rich frame forever (verified live: ticks frozen at 00758 while the LCD
# home screen showed "0s"), so a stale WAIT state would press OK in idle and
# open the device MENU - after which MC+/MC- navigate the menu instead of
# changing the temperature (manual p.7-8).
STOP_STATES = ("PULLING", "BREWING", "FLUSHING")
# States in which the setpoint keys act on pump power, not temperature
# (manual p.7: buttons change temperature with the pump off, pump power with
# the pump on).
SETPOINT_LOCKED_STATES = ("PULLING", "BREWING", "FLUSHING", "STEAMING")

# --- verified setpoint writing ------------------------------------------
# One MC+/MC- press moves the target by the firmware increment (0.1/1.0/10 K),
# which is measured from the first observed read-back. The walk below keeps
# presses bounded and verifies every round, so a wrong assumption can never
# overshoot more than one batch.
SETPOINT_MIN = 80.0
SETPOINT_MAX = 100.0
SETPOINT_TOLERANCE = 0.05  # 0.1 K is the finest step the firmware offers
SETPOINT_STEP_TIMEOUT = 1.0  # s to wait for a rich frame with the new value
SETPOINT_SETTLE_TIME = 0.25  # s without change = value settled
SETPOINT_PRESS_GAP = 0.1  # s between single presses inside one batch
SETPOINT_MAX_BATCH = 10  # presses per verified round
SETPOINT_MAX_PRESSES = 250  # hard budget per service call
SETPOINT_OPERATION_TIMEOUT = 60.0  # s, then abort with an error
SETPOINT_NO_PROGRESS = 3  # rounds without any change -> firmware refuses
WAKE_SETPOINT_TIMEOUT = 8.0  # s to wait for a setpoint after MCa

# Rich frames keep the last shot's tick counter while the machine is idle, so
# WAIT is only reported for this long after the last running ('*') frame.
WAIT_GRACE = 60.0  # s

# Ready/heating follows the firmware's own signalling tolerance: the PID menu
# defines the READY signal as "temperature within +/-1.0 C of setpoint"
# (manual p.196). Overridden from Setup > PID 1 > Signal when available.
HEATING_THRESHOLD = 1.0  # K
# The live eco target can differ slightly from the stored value (measured
# 81.3 C live vs 82.3 C in Setup > PID 1 > Eco), so allow a small window.
ECO_TOLERANCE = 1.5  # K

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

# Vendor Status-Monitor XML: the flow-meter fields carry a per-minute raw value
# with factor 0.0166 (= 1/60), so raw * 0.0166 is ml/s. The scale's own flow
# (g/s) is a different field and only exists with a paired Bluetooth scale.
FLOW_METER_FACTOR = 0.0166

# Platforms exposed in Phase 1.
PLATFORMS = ("sensor", "button", "number", "text", "switch")

# Derived machine status (sensor.ito_leva_status). These are the states a user
# can act on; MENU/FLUSHING are not visible in rich mode (the firmware streams
# either rich data OR the display, never both - verified live: MCr -> 0 LCD
# frames, MC@ -> LCD frames and no rich frames).
STATUS_OFFLINE = "OFFLINE"
STATUS_STANDBY = "STANDBY"
STATUS_ECO = "ECO"
STATUS_HEATING = "HEATING"
STATUS_COOLING = "COOLING"
STATUS_READY = "READY"
STATUS_BREWING = "BREWING"
STATUS_WAIT = "WAIT"
# Normal operating phases that override the brew setpoint. They are not errors:
# the firmware refuses setpoint keys while they run (manual p.7), so the
# integration must refuse too instead of pressing into the program.
STATUS_WARMUP = "WARM-UP"
STATUS_STEAM = "STEAM"

# A live setpoint this close to PID 1 > Ramp&Soak > Warm-Up > 1 > Target (and
# clearly different from the stored brew setpoint) means the warm-up override
# is active. Live reference: stored 94.0 C, warm-up target 110.0 C.
OVERRIDE_TOLERANCE = 0.6  # K
# Steam heating: the PID is bypassed, so the boiler climbs above the brew
# setpoint at high output. Below this output the machine is just cooling down.
STEAM_MIN_OUTPUT = 50  # %

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
    "status": "sensor.ito_leva_status",
    "connection": "sensor.ito_leva_connection",
    "setup_pid_control": "sensor.ito_leva_setup_pid_control",
    "setup_pid_setpoint": "sensor.ito_leva_setup_pid_setpoint",
    "setup_temp_type": "sensor.ito_leva_setup_temp_type",
    "setpoint_number": "number.ito_leva_setpoint",
    "connection_switch": "switch.ito_leva_connection",
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
