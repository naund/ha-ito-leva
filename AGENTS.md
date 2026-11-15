# AGENTS.md

## What this repo is

HACS-capable custom integration for the **ITO Leva!** espresso machine plus a
stock-card dashboard. The machine is a `leva! (3.3)` module reachable over
Telnet (`192.168.178.21:23` in this household); the integration speaks the
vendor's MC protocol over one persistent TCP connection.

- Target Home Assistant: **2026.10.4** (same version as the live instance).
- `custom_components/ito_leva/` — integration (`sensor`, `button`, `number`, `text`).
- `dashboards/ito-leva.yaml` — dashboard; its entity IDs are part of the contract.
- `tests/test_parsers.py` — pure protocol/parser tests, no HA runtime needed.
- `tests/integration/` — real HA integration tests with a fake device.
- Protocol sources: `../home-assistant/Ranchilio.md` and the `Leva-Companion`
  reference (`/home/nils/Dokumente/Projekte/ito/Leva-Companion/proxy.js`).
  Do not invent commands or field offsets.

## Setup and tests

Use the project-local venv (gitignored):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
```

Run everything (offline + HA integration):

```bash
.venv/bin/python -m pytest
```

Offline parser tests with the system Python (integration tests are skipped
when the HA test plugin is not installed):

```bash
python3 -m pytest tests/test_parsers.py
```

`requirements_test.txt` pins `pytest-homeassistant-custom-component==0.13.375`,
which in turn pins Home Assistant 2026.10.4. Keep that pin aligned with the
live HA version; do not upgrade one without the other.

The integration harness (`tests/integration/conftest.py`) copies the component
into a throw-away HA config dir for every test. `FakeIto` (`fake_ito.py`)
simulates the MC framing, shot state, standby/eco and the MCu dump. Do not
replace it with mocks of the integration classes; tests should drive HA
services and inspect entity registry/state, as a user would.

## Live-device safety

- Read-only protocol checks are always fine; they run from the LAN against
  `192.168.178.21:23`.
- Physical actuators (start/stop, flush, backflush, descale, pump) only with
  explicit user approval for that specific action.
- **Never** trigger FLUSH, BACKFLUSH or DESCALE without a separate, explicit
  confirmation. Shortcut 1 is FLUSH on this machine.
- Leave the machine in a sensible state when done: awake, `IDLE`, setpoint
  `94.2 °C`, around 94 °C (after an eco test, send `MCa` to restore it).
- The scale (Waage) is not connected in this household. `MCcDOSE` is therefore
  rejected with `[SCALE]`; that is correct firmware behaviour, not a bug in the
  integration.

## Verified protocol facts (do not regress)

- Handshake: TCP connect → `\nMC?\r` → `XML=…` → `MCr\r` → first `{` frame.
- Keepalive: bare `\n` every 7 s; reconnect after 3 s.
- Rich frame fields are **1-based frame positions**; body offset = frame − 2.
  - `Pos 3` profile marker (`-` idle, `*` shot, letter = profile)
  - `Pos 5–7` PID 1 control type (`PID` / `OFF`), vendor `at=4`
  - `Pos 14–18` PID 1 setpoint, vendor `at=13` (live: `094.2`)
  - `Pos 153–155` volume, `156–160` ticks (100 Hz), `175–180` group temp,
    `198–202` pressure, `203–212` band, `227–231` weight, `232–236` flow,
    `237–281` profile names.
- A fully `?`-filled rich frame means **STANDBY**; the machine keeps streaming
  unusable frames instead of going silent.
- LCD frames are `<BKL + 4×16>`; `\x04`→`▶`, `\x05`→`✓`, `\xDF`→`°`.
  LCD rows/temperature/pressure are a fallback and must survive rich frames.
- Start: wake from STANDBY (`MCa`, re-arm with `MCr`), leave MENU with CANCEL,
  then fire the active dose with **`MCcDOSE`**. Never use Shortcut 1 (`\x05`):
  it starts FLUSH on this machine.
- Stop: `OK` (`\x03`) **only** in `PULLING`/`BREWING`/`FLUSHING`/`WAIT`.
  Pressing OK while idle opens the device MENU.
- Standby: `MCE` (short form). Eco: `MCe` (short form, lowers setpoint to
  ~81.3 °C while staying awake). The long `MCcSTANDBY` / `MCcECO` forms are
  ignored by the firmware.
- Setpoint is relative only (`MC+` / `MC-` = 0.1 °C per step). Parse the
  absolute value first; never send a blind single nudge.
- `MCc…` commands are sent as one line (`MCc<name>\r`). Virtual buttons are
  `MC<byte>\r` + `MC\x00\r` after 30 ms.

## Entities, IDs and migration

- Dashboard/automation IDs are fixed in `const.ENTITY_IDS`
  (`sensor.ito_leva_temp`, `number.ito_leva_setpoint`,
  `text.ito_leva_lcd_mirror`, `button.ito_leva_start_shot`, …).
- Platforms set `entity_id` from that map and `has_entity_name = True` so
  Friendly Names come from `translations/*.json` while IDs stay stable.
- `ConfigFlow.VERSION = 2`; `async_migrate_entry()` rewrites v1 host-based IDs
  (e.g. `sensor.ito_leva_127_0_0_1_5`) to the stable IDs. Keep the map and the
  migration in sync when adding an entity.
- New entities also need a translation key in both `de.json` and `en.json`
  and an entry in the inventory test.
- `single_config_entry` is set in the manifest. Services are registered once
  globally and removed only when the last entry unloads.
- Fast telemetry sensors should stay out of the recorder (see README).

## Setup/robustness expectations

- `async_setup_entry()` waits for the rich handshake (`CONNECT_TIMEOUT`); an
  unreachable host must raise `ConfigEntryNotReady` after cleanly stopping the
  client. The config flow performs the same connection test.
- Rich frames are authoritative for numerics; a fresh LCD-only state
  (`HEATING`, `STANDBY`, `MENU`, `FLUSHING`) may briefly win over rich `IDLE`.
- Alarms (`[REFILL]`, `[DSCALE]`, `[BACKFL]`, `[SCALE]`, …) update the alarm
  sensor and a `persistent_notification`; they age out after
  `ALARM_CLEAR_TIMEOUT` if the firmware stops repeating them.
- `parse_mcu_tree()` must ignore rich/LCD frames captured during an MCu dump.

## Style

- Pure protocol/parsing logic belongs in `parsers.py` / `models.py` (no HA
  imports); HA coupling stays in coordinator/platform modules.
- Keep comments explanatory where a value comes from the vendor XML, proxy.js
  or a live measurement; future agents should be able to trace every magic
  number.
- Use `entity_id`, never `device_id`, in automations/scripts/dashboards.
- Dashboard changes use stock cards only; no custom card dependency.
