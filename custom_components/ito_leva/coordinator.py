"""Push coordinator: owns the TCP client, throttles HA state, pages HA.

Design (Ranchilio.md Phase 1):
* exactly ONE persistent connection per config entry (single shared ITO
  menu state -> client.lock serialises long ops)
* raw 10 Hz frames update an in-memory ring buffer (Phase 2 chart source)
  but HA entities refresh at most every TELEMETRY_THROTTLE (1 Hz)
* rich frames win over LCD frames; LCD fills setpoint + profile name gaps
* the connection can be paused with switch.ito_leva_connection; the desired
  state is persisted so a restart keeps the integration detached
* derived `status` (READY/HEATING/COOLING/STEAM/WARM-UP/ECO/STANDBY/BREWING/
  WAIT/OFFLINE) for the status sensor: rich mode has no display stream, so it
  is computed from the setpoint, the boiler temperature and the PID output.
  WARM-UP and STEAM are normal phases in which the firmware refuses setpoint
  keys; they are reported and enforced, never treated as errors.
* scale flow (g/s) is derived from weight deltas here (proxy.js
  _deriveWeightFlow): needs >= 0.3 s between samples, resets on cup change
* alarm tokens -> `alarm` sensor + `persistent_notification` (Phase 1 UX)
* 30 s temperature carry-forward so mid-shot LCD dropouts don't blank temp

Setpoint writes are closed-loop (`async_set_setpoint`): the firmware increment
is not fixed (MC# = 0.1/1.0/10 K), so the step is measured from the read-back
and every round is verified. A burst without verification is what made the
target jump to the limits in the field test.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from . import parsers
from .client import ItoLevaClient
from .const import (
    ALARM_CLEAR_TIMEOUT,
    DOMAIN,
    LCD_STATE_HOLD,
    OVERRIDE_TOLERANCE,
    RING_BUFFER_MAXLEN,
    SETPOINT_LOCKED_STATES,
    SETPOINT_MAX,
    SETPOINT_MAX_BATCH,
    SETPOINT_MAX_PRESSES,
    SETPOINT_MIN,
    SETPOINT_NO_PROGRESS,
    SETPOINT_OPERATION_TIMEOUT,
    SETPOINT_PRESS_GAP,
    SETPOINT_SETTLE_TIME,
    SETPOINT_STEP_TIMEOUT,
    SETPOINT_TOLERANCE,
    SETUP_INITIAL_DELAY,
    STATUS_OFFLINE,
    STATUS_STEAM,
    STATUS_WARMUP,
    STEAM_MIN_OUTPUT,
    TELEMETRY_THROTTLE,
    WAIT_GRACE,
    WAKE_SETPOINT_TIMEOUT,
)
from .models import Telemetry

_LOGGER = logging.getLogger(__name__)

SIGNAL_TELEMETRY = f"{DOMAIN}_telemetry"
SIGNAL_CONNECTION = f"{DOMAIN}_connection"
SIGNAL_ALARM = f"{DOMAIN}_alarm"
SIGNAL_SETUP = f"{DOMAIN}_setup"

# LCD-only states that should win over a plain rich-frame IDLE for a moment.
_LCD_PRIORITY_STATES = {"HEATING", "STANDBY", "MENU", "FLUSHING", "STEAMING"}

# Setup-tree leaves are rendered with units ("82.3°", "ERR>1.0°"); pull the
# first number out of them.
_SETUP_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

_CONNECTION_STORE_VERSION = 1


class ItoLevaCoordinator:
    """Bridges ItoLevaClient callbacks into HA dispatcher signals."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        host: str,
        port: int,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.host = host
        self.port = port
        self.data = Telemetry()
        self.setup_tree: dict[str, Any] = {}
        self.connected = False
        # switch.ito_leva_connection: when False the TCP client is stopped but
        # the config entry stays loaded, so the switch can reconnect it.
        self.connection_enabled = True
        self.ring: deque[dict] = deque(maxlen=RING_BUFFER_MAXLEN)
        self._store: Store[dict[str, Any]] = Store(
            hass, _CONNECTION_STORE_VERSION, f"{DOMAIN}.{entry_id}_connection"
        )
        # Persisted: {"enabled": bool, "eco_setpoint": float, "brew_setpoint":
        # float, "warmup_target": float}. The setup values are cached from the
        # MCu dump so status ECO and the warm-up override detection work after a
        # restart without dumping the setup again.
        self._store_data: dict[str, Any] = {
            "enabled": True,
            "eco_setpoint": None,
            "brew_setpoint": None,
            "warmup_target": None,
        }
        self._eco_cached: float | None = None
        self._last_push = 0.0
        self._last_temp: float | None = None
        self._last_temp_ts = 0.0
        self._lcd_state: str | None = None
        self._lcd_state_ts = 0.0
        self._alarm_ts = 0.0
        self._last_shot_ts = 0.0  # last frame with the '*' running marker
        self._flow_base: tuple[float, float] | None = None  # (weight, monotonic)
        self._flow_value = 0.0
        # Measured size of one MC+/MC- press; None until the first read-back.
        self.setpoint_increment: float | None = None
        self.client = ItoLevaClient(
            host,
            port,
            on_telemetry=self._on_frame,
            on_alarm=self._on_alarm,
            on_connection=self._on_connection,
        )

    # -- lifecycle ------------------------------------------------------

    @property
    def available(self) -> bool:
        """Entities are unavailable only when the user detached the machine."""
        return self.connection_enabled

    async def async_load_connection_state(self) -> None:
        """Restore the connect/disconnect switch across restarts."""
        stored = await self._store.async_load()
        if stored:
            self._store_data.update(stored)
        self.connection_enabled = bool(self._store_data.get("enabled", True))
        eco = self._store_data.get("eco_setpoint")
        self._eco_cached = float(eco) if eco is not None else None
        if not self.connection_enabled:
            self.data.status = STATUS_OFFLINE

    async def _async_save_store(self) -> None:
        await self._store.async_save(self._store_data)

    async def async_start(self) -> None:
        if self.connection_enabled:
            await self.client.async_start()

    async def async_stop(self) -> None:
        await self.client.async_stop()

    async def async_set_connection(self, enabled: bool) -> None:
        """Start/stop the TCP client from switch.ito_leva_connection."""
        if enabled == self.connection_enabled:
            return
        self.connection_enabled = enabled
        self._store_data["enabled"] = enabled
        await self._async_save_store()
        if enabled:
            _LOGGER.info("ITO Leva! connection enabled")
            await self.client.async_start()
        else:
            _LOGGER.info("ITO Leva! connection disabled")
            await self.client.async_stop()
            self.connected = False
            self.client.state = "UNKNOWN"
            self.data.state = "UNKNOWN"
            self.data.status = STATUS_OFFLINE
        async_dispatcher_send(self.hass, SIGNAL_CONNECTION)

    async def async_refresh_setup(self) -> None:
        """Read one MCu dump and notify the setup sensors."""
        lines = await self.client.dump_setup()
        self.setup_tree = parsers.parse_mcu_tree(lines)
        changed = False
        for key, path in (
            ("eco_setpoint", ("PID 1", "Eco")),
            ("brew_setpoint", ("PID 1", "Setpoint")),
            ("warmup_target", ("PID 1", "Ramp&Soak", "Warm-Up", "1", "Target")),
        ):
            value = self._setup_number(*path)
            if value is not None:
                self._store_data[key] = value
                changed = True
        if self._setup_number("PID 1", "Eco") is not None:
            self._eco_cached = self._setup_number("PID 1", "Eco")
        if changed:
            await self._async_save_store()
        _LOGGER.info("Setup tree refreshed (%d lines)", len(lines))
        async_dispatcher_send(self.hass, SIGNAL_SETUP)

    async def async_initial_setup_refresh(self) -> None:
        """One setup read after the first start (eco setpoint, setup sensors).

        Delayed a little so it cannot race the first frames or a manual
        reporting-mode change; the result is cached in the store, so this runs
        once and not on every restart.
        """
        await asyncio.sleep(SETUP_INITIAL_DELAY)
        if not self.connection_enabled or not self.connected:
            return
        try:
            await self.async_refresh_setup()
        except Exception:  # bookkeeping must never break the config entry
            _LOGGER.exception("Initial setup dump failed")

    # -- setup-derived thresholds ----------------------------------------

    def _setup_number(self, *path: str) -> float | None:
        """Numeric leaf of the MCu setup tree, e.g. ("PID 1", "Eco") -> 82.3."""
        node: Any = self.setup_tree
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        if not isinstance(node, str):
            return None
        match = _SETUP_NUMBER_RE.search(node)
        return float(match.group(0)) if match else None

    @property
    def eco_setpoint(self) -> float | None:
        """Setup > PID 1 > Eco: the setpoint used in energy-saver mode."""
        value = self._setup_number("PID 1", "Eco")
        return value if value is not None else self._eco_cached

    @property
    def brew_setpoint(self) -> float | None:
        """Setup > PID 1 > Setpoint: the stored brew target (not the override)."""
        value = self._setup_number("PID 1", "Setpoint")
        if value is not None:
            return value
        cached = self._store_data.get("brew_setpoint")
        return float(cached) if cached is not None else None

    @property
    def warmup_target(self) -> float | None:
        """Setup > PID 1 > Ramp&Soak > Warm-Up > 1 > Target (live: 110.0 C).

        Step 2 hands the boiler back to the PID; step 1 is the phase in which
        the rich frame reports this target instead of the brew setpoint.
        """
        value = self._setup_number(
            "PID 1", "Ramp&Soak", "Warm-Up", "1", "Target"
        )
        if value is not None:
            return value
        cached = self._store_data.get("warmup_target")
        return float(cached) if cached is not None else None

    @property
    def override_reason(self) -> str | None:
        """Program currently overriding the brew setpoint, or None.

        The warm-up phase is recognised by the effective setpoint (which the
        rich frame always carries) matching the configured warm-up target while
        differing from the stored brew setpoint. This is a normal operating
        phase, not an error - but the firmware refuses setpoint keys during it
        (manual p.7, footnote 2).
        """
        live = self.data.setpoint
        warmup = self.warmup_target
        if live is None or warmup is None:
            return None
        brew = self.brew_setpoint
        if abs(live - warmup) > OVERRIDE_TOLERANCE:
            return None
        if brew is not None and abs(live - brew) <= OVERRIDE_TOLERANCE:
            return None  # warm-up target equals the brew target: not an override
        return STATUS_WARMUP

    @property
    def steam_active(self) -> bool:
        """Steam heating: boiler above target while the PID still heats at 100%."""
        temp, setpoint = self.data.temp, self.data.setpoint
        if temp is None or setpoint is None:
            return False
        if temp <= setpoint + self.heating_threshold:
            return False
        output = self.data.pid_output
        return output is not None and output >= STEAM_MIN_OUTPUT

    def setpoint_lock_reason(self) -> str | None:
        """Why a setpoint write is currently not possible (None = writable)."""
        reason = self.override_reason
        if reason:
            return reason
        if self.steam_active:
            return STATUS_STEAM
        if self.client.state in SETPOINT_LOCKED_STATES:
            return "PUMP"
        return None

    @property
    def heating_threshold(self) -> float:
        """Firmware READY tolerance from Setup > PID 1 > Signal ("ERR>1.0°")."""
        threshold = self._setup_number("PID 1", "Signal")
        return threshold if threshold and threshold > 0 else 1.0

    # -- client callbacks (run in the socket task; only touch plain attrs) --

    def _on_connection(self, connected: bool) -> None:
        self.connected = connected
        if not connected:
            # No frames will arrive until the reconnect, so do not report a
            # stale READY/BREWING as if the machine were still reachable.
            self.client.state = "UNKNOWN"
            self.data.state = "UNKNOWN"
            self.data.status = STATUS_OFFLINE
        self.hass.add_job(async_dispatcher_send, self.hass, SIGNAL_CONNECTION)

    def _on_alarm(self, token: str, reason: str) -> None:
        self.data.alarm = token
        self._alarm_ts = time.monotonic()
        self.hass.add_job(async_dispatcher_send, self.hass, SIGNAL_ALARM, token)
        self.hass.add_job(self._notify_alarm, token, reason)

    async def _notify_alarm(self, token: str, reason: str) -> None:
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": f"ITO Leva! {token}",
                "message": f"Firmware meldet {token}: {reason}.",
                "notification_id": f"{DOMAIN}_{token.lower()}",
            },
            blocking=False,
        )

    def _on_frame(self, sample: dict, is_rich: bool) -> None:
        now = time.monotonic()
        self.ring.append({"t": now, **sample})  # full-rate, memory only
        merged = self._merge(sample, is_rich, now)
        # The client's busy guards must see the same corrected state as HA
        # (the parser alone reports WAIT forever from the frozen tick counter).
        self.client.state = merged.state
        # Throttle: entities see at most 1 Hz; the ring keeps every frame.
        if now - self._last_push >= TELEMETRY_THROTTLE:
            self._last_push = now
            self.hass.add_job(async_dispatcher_send, self.hass, SIGNAL_TELEMETRY)

    def _merge(self, sample: dict, is_rich: bool, now: float) -> Telemetry:
        data = self.data
        data.raw = sample
        state = sample.get("state")

        # Clear alarms that the firmware stopped repeating.
        if data.alarm is not None and now - self._alarm_ts > ALARM_CLEAR_TIMEOUT:
            data.alarm = None

        if is_rich:
            # Rich frames carry the authoritative numeric fields.
            for key in ("pressure", "temp", "volume", "weight", "flow_ml", "pid_output"):
                if sample.get(key) is not None:
                    setattr(data, key, sample[key])
            if sample.get("setpoint") is not None:
                data.setpoint = sample["setpoint"]
            if sample.get("pid_on") is not None:
                data.pid_on = sample["pid_on"]
            if sample.get("pid_control") is not None:
                data.pid_control = sample["pid_control"]
            if sample.get("timer") is not None:
                data.timer = sample["timer"]
            data.shot_running = bool(sample.get("shot_running"))
            name = sample.get("active_profile_name") or sample.get("active_profile")
            if name:
                data.active_profile = name
            if data.shot_running:
                self._last_shot_ts = now
            elif (
                state == "WAIT"
                and now - self._last_shot_ts > WAIT_GRACE
            ):
                # The rich frame keeps the last shot's ticks forever (verified
                # live: 00758 while the LCD showed "0s"). Without a recent shot
                # this is idle, not a pending result screen.
                state = "IDLE"
            if state == "STANDBY":
                # Standby frames are '?' filled; do not keep a stale setpoint
                # that would make a setpoint write hit a sleeping machine.
                data.setpoint = None
            if state == "IDLE" and data.timer is not None:
                data.timer = 0.0  # frozen last-shot ticks are not a live timer
            # A plain rich IDLE must not hide a fresh LCD state such as
            # HEATING/STANDBY/MENU; running states from rich still win.
            if state:
                if (
                    state == "IDLE"
                    and self._lcd_state in _LCD_PRIORITY_STATES
                    and now - self._lcd_state_ts < LCD_STATE_HOLD
                ):
                    data.state = self._lcd_state
                else:
                    data.state = state
        else:
            # Persist the LCD mirror: rich frames must never wipe it.
            if sample.get("rows"):
                data.lcd_rows = list(sample["rows"])
            previous_lcd_state = self._lcd_state
            if state:
                self._lcd_state = state
                self._lcd_state_ts = now
                if state != "IDLE":
                    data.state = state
                elif data.state in ("UNKNOWN", "IDLE", previous_lcd_state):
                    # Let an LCD IDLE clear a stale LCD-only state, but do not
                    # let it override a running rich state.
                    data.state = "IDLE"
            # LCD is the only source in MC@ mode; fill gaps without touching
            # values that the rich stream has just refreshed.
            for key in ("pressure", "temp", "volume", "weight", "flow_ml"):
                if sample.get(key) is not None and getattr(data, key) is None:
                    setattr(data, key, sample[key])
            if sample.get("timer") is not None and data.timer is None:
                data.timer = sample["timer"]
            name = sample.get("active_profile_name")
            # On status screens (WARM-UP/MENU/STANDBY/FLUSH) row 1 is a
            # heading, not a profile name; only home/shot screens carry one.
            if name and state in ("IDLE", "PULLING", "WAIT"):
                data.active_profile = name
        # 30 s temp carry-forward (proxy.js _lastTemp1) for LCD dropouts.
        if data.temp is not None:
            self._last_temp, self._last_temp_ts = data.temp, now
        elif self._last_temp is not None and now - self._last_temp_ts < 30.0:
            data.temp = self._last_temp
        # Status for sensor.ito_leva_status (READY/HEATING/ECO/...).
        data.status = parsers.derive_status(
            connected=self.connected,
            state=data.state,
            temp=data.temp,
            setpoint=data.setpoint,
            eco_setpoint=self.eco_setpoint,
            override=self.override_reason,
            pid_output=data.pid_output,
            heating_threshold=self.heating_threshold,
        )
        # Scale flow g/s from weight deltas (proxy.js _deriveWeightFlow).
        data.flow = self._derive_flow(data.state, data.weight, now)
        return data

    def _derive_flow(
        self, state: str, weight: float | None, now: float
    ) -> float:
        if state == "STANDBY" or weight is None:
            self._flow_base = (weight or 0.0, now)
            self._flow_value = 0.0
            return 0.0
        if self._flow_base is None:
            self._flow_base = (weight, now)
            return 0.0
        base_weight, base_ts = self._flow_base
        delta_w, delta_t = weight - base_weight, now - base_ts
        if delta_w < -0.5:  # cup removed / tared -> re-baseline
            self._flow_base = (weight, now)
            self._flow_value = 0.0
        elif delta_t >= 0.3 and delta_w >= 0:
            self._flow_value = round(delta_w / delta_t, 2)
            self._flow_base = (weight, now)
        return self._flow_value

    # -- setpoint (closed loop) -------------------------------------------

    async def async_set_setpoint(self, target: float) -> float:
        """Move the brew setpoint to `target`, verified against the read-back.

        Returns the value actually reached. Raises HomeAssistantError when the
        machine cannot be changed (detached, busy, or the firmware refuses the
        keys because a program overrides the setpoint - manual p.7).

        The firmware's step size is configurable (`MC#`: 0.1/1.0/10 K), so the
        first observed change is used to measure the increment; all further
        rounds are sized from it and verified.
        """
        if not self.connection_enabled:
            raise HomeAssistantError(
                "Die Verbindung ist über switch.ito_leva_connection getrennt"
            )
        if not self.connected:
            raise HomeAssistantError("Keine Verbindung zur ITO Leva!")
        reason = self.setpoint_lock_reason()
        if reason == STATUS_WARMUP:
            target = self.warmup_target
            _LOGGER.info(
                "Setpoint change refused: warm-up program active (target %s C)",
                target,
            )
            raise HomeAssistantError(
                "Sollwert ist während des Aufwärmprogramms gesperrt"
                + (f" (Ziel {target:.1f} °C)" if target is not None else "")
                + ". Nach dem Aufwärmen wieder änderbar."
            )
        if reason == STATUS_STEAM:
            _LOGGER.info("Setpoint change refused: steam heating active")
            raise HomeAssistantError(
                "Sollwert ist beim Aufheizen für Dampf gesperrt. "
                "Nach dem Dampfbezug wieder änderbar."
            )
        if reason == "PUMP":
            raise HomeAssistantError(
                "Sollwert wird nicht geändert: die Tasten wirken bei laufender "
                f"Pumpe auf die Pumpenleistung (Status {self.client.state})"
            )
        target = min(SETPOINT_MAX, max(SETPOINT_MIN, float(target)))

        async with self.client.lock:
            current = await self._ensure_setpoint()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + SETPOINT_OPERATION_TIMEOUT
            presses = 0
            refused_rounds = 0
            best = current
            best_err = abs(target - current)

            while loop.time() < deadline and presses < SETPOINT_MAX_PRESSES:
                if self.client.state in SETPOINT_LOCKED_STATES:
                    # A shot started mid-walk: the keys now change pump power.
                    raise HomeAssistantError(
                        "Sollwert-Änderung abgebrochen: die Pumpe läuft "
                        f"(Status {self.client.state})"
                    )
                current = self.data.setpoint
                if current is None:
                    raise HomeAssistantError("Sollwert ist unbekannt (Standby?)")
                err = abs(target - current)
                if err <= SETPOINT_TOLERANCE:
                    await self._remember_brew_setpoint(current)
                    return current
                if err > best_err and presses > 0:
                    # The last round crossed the target: the increment cannot
                    # hit it exactly. Walk back to the closest value seen.
                    _LOGGER.info(
                        "Setpoint %s not reachable with increment %s; "
                        "returning to %s",
                        target, self.setpoint_increment, best,
                    )
                    await self._step_towards(best, current)
                    reached = self.data.setpoint or best
                    await self._remember_brew_setpoint(reached)
                    return reached
                if err < best_err:
                    best, best_err = current, err

                if self.setpoint_increment:
                    wanted = max(1, int(err / self.setpoint_increment))
                    batch = min(wanted, SETPOINT_MAX_BATCH)
                else:
                    batch = 1  # measure the increment before sending more
                up = target > current
                for _ in range(batch):
                    self.client.press_setpoint_step(up)
                    presses += 1
                    await asyncio.sleep(SETPOINT_PRESS_GAP)
                changed = await self._wait_setpoint_change(
                    current, SETPOINT_STEP_TIMEOUT
                )
                if not changed:
                    refused_rounds += 1
                    _LOGGER.warning(
                        "Setpoint press made no progress (%d/%d, state=%s)",
                        refused_rounds,
                        SETPOINT_NO_PROGRESS,
                        self.client.state,
                    )
                    if refused_rounds >= SETPOINT_NO_PROGRESS:
                        raise HomeAssistantError(
                            "Die Maschine nimmt keine Sollwert-Änderung an "
                            "(Programm-/Ramp&Soak-Override oder Tasten im Menü)"
                        )
                    continue
                refused_rounds = 0
                if self.setpoint_increment is None:
                    new = self.data.setpoint
                    if new is not None and abs(new - current) > 0:
                        self.setpoint_increment = round(abs(new - current), 3)
                        _LOGGER.info(
                            "Setpoint increment measured: %.2f K per press",
                            self.setpoint_increment,
                        )
                await self._settle_setpoint()

            raise HomeAssistantError(
                f"Sollwert {target} °C nicht erreicht "
                f"(zuletzt {self.data.setpoint}, {presses} Tasten)"
            )

    async def _remember_brew_setpoint(self, value: float) -> None:
        """Keep the cached Setup > PID 1 > Setpoint in sync after a write.

        Setpoint keys are auto-saved by the firmware, so the stored value moves
        with them; without this the override detection would compare against a
        stale target until the next MCu dump.
        """
        self._store_data["brew_setpoint"] = round(float(value), 2)
        await self._async_save_store()

    async def _ensure_setpoint(self) -> float:
        """Return a known setpoint, waking the machine if it is in standby."""
        current = self.data.setpoint
        if current is not None:
            return current
        _LOGGER.info("Setpoint unknown (standby) -> wake, then re-read")
        await self.client.wake()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WAKE_SETPOINT_TIMEOUT
        while loop.time() < deadline:
            await asyncio.sleep(0.1)
            if self.data.setpoint is not None:
                return self.data.setpoint
        raise HomeAssistantError(
            "Sollwert ist noch unbekannt; bitte warten bis Telemetrie empfangen wurde"
        )

    async def _wait_setpoint_change(self, before: float, timeout: float) -> bool:
        """Wait until the rich frame reports a different setpoint."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(0.05)
            current = self.data.setpoint
            if current is not None and abs(current - before) > 0.001:
                return True
        return False

    async def _settle_setpoint(self) -> None:
        """Wait until two consecutive samples report the same setpoint."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETPOINT_STEP_TIMEOUT
        last = self.data.setpoint
        stable_since = loop.time()
        while loop.time() < deadline:
            await asyncio.sleep(0.05)
            current = self.data.setpoint
            if current != last:
                last = current
                stable_since = loop.time()
            elif loop.time() - stable_since >= SETPOINT_SETTLE_TIME:
                return

    async def _step_towards(self, target: float, current: float) -> None:
        """Single verified corrections towards `target` (nearest reachable)."""
        for _ in range(SETPOINT_MAX_BATCH):
            if abs(target - current) <= SETPOINT_TOLERANCE:
                return
            self.client.press_setpoint_step(target > current)
            await asyncio.sleep(SETPOINT_PRESS_GAP)
            if not await self._wait_setpoint_change(current, SETPOINT_STEP_TIMEOUT):
                return
            await self._settle_setpoint()
            current = self.data.setpoint if self.data.setpoint is not None else current
