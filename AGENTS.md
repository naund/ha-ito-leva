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
- **`MCr` and `MC@` are mutually exclusive** (measured live 2026-11): `MCr`
  streams only `{…}` rich frames (≈4 Hz), `MC@` only `<…>` LCD frames (≈10 Hz).
  Rich mode therefore has **no display state**: `MENU`, `FLUSHING`, `HEATING`
  and the LCD mirror only exist in `MC@` mode. Do not build status on the LCD
  stream unless you switch modes deliberately.
- **The tick counter stays frozen at the last shot** (live: `00758` in every
  frame while the LCD home screen showed `0s`). `timer > 0` alone is **not**
  WAIT; the coordinator only reports WAIT for `WAIT_GRACE` seconds after a
  frame with the `*` marker.
- LCD frames are `<BKL + 4×16>`; `\x04`→`▶`, `\x05`→`✓`, `\xDF`→`°`.
  LCD rows/temperature/pressure are a fallback and must survive rich frames.
- Start: wake from STANDBY (`MCa`, re-arm with `MCr`), leave MENU with CANCEL,
  then fire the active dose with **`MCcDOSE`**. Never use Shortcut 1 (`\x05`):
  it starts FLUSH on this machine.
- Stop: `OK` (`\x03`) **only** in `PULLING`/`BREWING`/`FLUSHING`
  (`const.STOP_STATES`). Pressing OK while idle opens the device MENU, and
  `MC+`/`MC-` inside the MENU navigate/edit instead of changing temperature.
  Never add `WAIT` back to that tuple.
- Standby: `MCE` (short form). Eco: `MCe` (short form, lowers setpoint to
  ~81.3 °C while staying awake; stored eco setpoint is `Setup > PID 1 > Eco`,
  live 82.3 °C). The long `MCcSTANDBY` / `MCcECO` forms are ignored.
- Setpoint keys: `MC+`/`MC-` = "Change setpoint" (manual p.227), but the step
  size comes from `MC#` and is **0.1, 1.0 or 10 K** — the vendor frame exposes
  it as the `increment` field (`at=163`, live `001.0`). Never convert an
  absolute target into a counted burst assuming 0.1 K; the coordinator
  measures the increment from the first read-back and verifies every round.
- Setpoint writes are **refused while a program overrides the setpoint**
  (warm-up, Ramp & Soak; manual p.7, footnote 2). This is a **normal phase, not
  an error**: do not send presses, do not raise a notification, log at INFO.
  The rich frame then shows the program's value (live: `110.0` during warm-up),
  not `Setup > PID 1 > Setpoint`.
  - Warm-up signature: `PID 1 > Ramp&Soak > Warm-Up > 1 > Target` (live
    `110.0`, step 2 hands back to `PID` after 6m30s) == live setpoint and
    != stored brew setpoint. The top-level `Warm-Up` program
    (`Autostart 40.0°`, vent + flush steps) purges the group afterwards.
  - Steam signature: boiler above setpoint + tolerance **and** `at=18`
    (Heat PID) >= 50 % — the PID is bypassed for steaming. Output 0 means the
    boiler is only cooling down (COOLING, not STEAM).
  - `number.ito_leva_setpoint` exposes `locked` / `lock_reason` for the UI.
  With the pump running, `MC+`/`MC-` change pump power, not temperature.
- `MCc…` commands are sent as one line (`MCc<name>\r`). Virtual buttons are
  `MC<byte>\r` + `MC\x00\r` after 30 ms.
- Pump pressure controller internals live in the rich frame (vendor XML, frame
  pos = `at` + 1): `Pressure SP` `at=192`, `Phase` `at=212`, `Phase (Bias)`
  `at=215`, `Phase (P)` `at=218`, `Phase (I)` `at=222`, `Heat PID` `at=18`.
  They are `?` unless a pressure-profiled shot runs, and must satisfy
  `phase = bias + P + I` (manual p.109). Log them with
  `tools/record_shot.py` (read-only; `--pressure-test` only uses `MCb`).
  A full PI tuning needs both ranges (< 2.5 bar and > 2.5 bar), a static
  pressure test and repeated shots - see README.

## Entities, IDs and migration

- Dashboard/automation IDs are fixed in `const.ENTITY_IDS`
  (`sensor.ito_leva_temp`, `sensor.ito_leva_status`,
  `number.ito_leva_setpoint`, `switch.ito_leva_connection`,
  `text.ito_leva_lcd_mirror`, `button.ito_leva_start_shot`, …).
- `switch.ito_leva_connection` pauses the TCP client without unloading the
  entry; the desired state is persisted in `Store` (`<domain>.<entry>_connection`).
  Entities are `unavailable` while detached, except the switch and the
  connection sensor.
- `sensor.ito_leva_status` is derived (`parsers.derive_status()`), not read
  from the machine: READY/HEATING/COOLING use the firmware's own ±1.0 K
  tolerance (`Setup > PID 1 > Signal`), ECO compares the live setpoint to
  `Setup > PID 1 > Eco` (cached in the store after the first MCu dump).
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
