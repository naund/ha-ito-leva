"""Push coordinator: owns the TCP client, throttles HA state, pages HA.

Design (Ranchilio.md Phase 1):
* exactly ONE persistent connection per config entry (single shared ITO
  menu state -> client.lock serialises long ops)
* raw 10 Hz frames update an in-memory ring buffer (Phase 2 chart source)
  but HA entities refresh at most every TELEMETRY_THROTTLE (1 Hz)
* rich frames win over LCD frames; LCD fills setpoint + profile name gaps
* scale flow (g/s) is derived from weight deltas here (proxy.js
  _deriveWeightFlow): needs >= 0.3 s between samples, resets on cup change
* alarm tokens -> `alarm` sensor + `persistent_notification` (Phase 1 UX)
* 30 s temperature carry-forward so mid-shot LCD dropouts don't blank temp
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .client import ItoLevaClient
from .const import (
    DOMAIN,
    RING_BUFFER_MAXLEN,
    TELEMETRY_THROTTLE,
)
from .models import Telemetry

_LOGGER = logging.getLogger(__name__)

SIGNAL_TELEMETRY = f"{DOMAIN}_telemetry"
SIGNAL_CONNECTION = f"{DOMAIN}_connection"
SIGNAL_ALARM = f"{DOMAIN}_alarm"


class ItoLevaCoordinator:
    """Bridges ItoLevaClient callbacks into HA dispatcher signals."""

    def __init__(self, hass: HomeAssistant, host: str, port: int) -> None:
        self.hass = hass
        self.host = host
        self.port = port
        self.data = Telemetry()
        self.setup_tree: dict[str, Any] = {}
        self.connected = False
        self.ring: deque[dict] = deque(maxlen=RING_BUFFER_MAXLEN)
        self._last_push = 0.0
        self._last_temp: float | None = None
        self._last_temp_ts = 0.0
        self._flow_base: tuple[float, float] | None = None  # (weight, monotonic)
        self._flow_value = 0.0
        self.client = ItoLevaClient(
            host,
            port,
            on_telemetry=self._on_frame,
            on_alarm=self._on_alarm,
            on_connection=self._on_connection,
        )

    async def async_start(self) -> None:
        await self.client.async_start()

    async def async_stop(self) -> None:
        await self.client.async_stop()

    # -- client callbacks (run in the socket task; only touch plain attrs) --

    def _on_connection(self, connected: bool) -> None:
        self.connected = connected
        self.hass.add_job(async_dispatcher_send, self.hass, SIGNAL_CONNECTION)

    def _on_alarm(self, token: str, reason: str) -> None:
        self.data.alarm = token
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
        # Throttle: entities see at most 1 Hz; the ring keeps every frame.
        if now - self._last_push >= TELEMETRY_THROTTLE:
            self._last_push = now
            self.hass.add_job(async_dispatcher_send, self.hass, SIGNAL_TELEMETRY)

    def _merge(self, sample: dict, is_rich: bool, now: float) -> Telemetry:
        data = self.data
        data.raw = sample
        if sample.get("state"):
            data.state = sample["state"]
        if is_rich:
            # Rich frames carry the authoritative numeric fields.
            for key in ("pressure", "temp", "volume", "weight", "flow_ml"):
                if sample.get(key) is not None:
                    setattr(data, key, sample[key])
            if sample.get("timer") is not None:
                data.timer = sample["timer"]
            data.shot_running = bool(sample.get("shot_running"))
            name = sample.get("active_profile_name") or sample.get("active_profile")
            if name:
                data.active_profile = name
        else:
            # LCD fallback fills setpoint/profile gaps; numerics only when
            # no fresh rich value exists (rich resyncs every frame anyway).
            if sample.get("pressure") is not None and data.pressure is None:
                data.pressure = sample["pressure"]
            name = sample.get("active_profile_name")
            if name:
                data.active_profile = name
        # 30 s temp carry-forward (proxy.js _lastTemp1) for LCD dropouts.
        if data.temp is not None:
            self._last_temp, self._last_temp_ts = data.temp, now
        elif self._last_temp is not None and now - self._last_temp_ts < 30.0:
            data.temp = self._last_temp
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
