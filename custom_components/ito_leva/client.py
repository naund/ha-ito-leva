"""Asyncio TCP client for the MC protocol (HA-dependent thin layer).

Protocol behaviour ported from proxy.js:
* connect -> `\\nMC?\\r`, on `XML=` send `MCr\\r`, first `{` frame -> ready
* keepalive bare `\\n` every 7 s (ready state only, resets ~10 s idle timer)
* latin-1/binary encoding, newline-delimited lines, reconnect after 3 s
* `asyncio.Lock` only around LONG operations (MCu dump); short button
  presses complete in ms and skip the lock (same rule as withItoLock)
* press = `MC<byte>\\r`, release `MC\\x00\\r` after 30 ms
* start = MCa-wake (x3) -> CANCEL out of MENU (x4) -> Shortcut[1] `\\x05`;
  stop = OK `\\x03`; after MCu/wake re-arm stream with `MCr\\r`

The client emits parsed frames via callbacks so `coordinator.py` (which owns
HA state) stays the only HA-coupled consumer. Pure parsing lives in
`parsers.py` and is unit-testable without HA.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Optional

from . import parsers
from .const import (
    BTN_CANCEL,
    BTN_OK,
    BTN_RELEASE,
    BTN_SHORTCUT_1,
    CMD_DUMP_SETUP,
    CMD_PROBE,
    CMD_RICH,
    CMD_WAKE,
    KEEPALIVE_INTERVAL,
    MCU_CAPTURE_TIMEOUT,
    PRESS_RELEASE_DELAY,
    RECONNECT_DELAY,
    SOCKET_TIMEOUT,
    START_MENU_EXITS,
    START_STEP_WAIT,
    START_WAKE_RETRIES,
    START_WAKE_WAIT,
)

_LOGGER = logging.getLogger(__name__)


class ItoLevaClient:
    """Owns the single persistent TCP connection to the ITO module."""

    def __init__(
        self,
        host: str,
        port: int,
        on_telemetry: Callable[[dict, bool], None] | None = None,
        on_alarm: Callable[[str, str], None] | None = None,
        on_connection: Callable[[bool], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_telemetry = on_telemetry  # (sample, is_rich)
        self.on_alarm = on_alarm  # (token, reason)
        self.on_connection = on_connection  # connected bool
        self.lock = asyncio.Lock()  # long-op mutex (MCu); short cmds skip it
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._handshake = "init"  # init | identify | rich | ready
        self._buffer = ""
        self._mcu_capture: list[str] | None = None
        self.recent_lines: deque[tuple[float, str]] = deque(maxlen=30)
        self.state = "UNKNOWN"  # last known machine state (busy checks)

    # -- lifecycle ------------------------------------------------------

    async def async_start(self) -> None:
        """Start background connect/read/keepalive tasks (idempotent)."""
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._connect_loop(), name="ito_leva_connect"),
            asyncio.create_task(self._keepalive_loop(), name="ito_leva_keepalive"),
        ]

    async def async_stop(self) -> None:
        """Stop background tasks and close the socket."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        await self._close_socket()

    async def _close_socket(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (OSError, asyncio.CancelledError):
                pass
        self._reader = self._writer = None
        self._handshake = "init"
        self._buffer = ""

    async def _connect_loop(self) -> None:
        """Hold the connection open; reconnect after RECONNECT_DELAY."""
        while self._running:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.warning("ITO connection failed: %s", err)
            if self._running:
                self._notify_connection(False)
                await asyncio.sleep(RECONNECT_DELAY)

    async def _connect_once(self) -> None:
        _LOGGER.info("Connecting to ITO at %s:%s", self.host, self.port)
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), SOCKET_TIMEOUT
        )
        self._handshake = "identify"
        self._write_line(CMD_PROBE)
        self._notify_connection(True)
        try:
            while self._running:
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise OSError("ITO closed the connection")
                self._buffer += chunk.decode("latin-1")
                *lines, self._buffer = self._buffer.split("\n")
                for raw in lines:
                    line = raw.strip()
                    if line:
                        self._handle_line(line)
        finally:
            await self._close_socket()

    async def _keepalive_loop(self) -> None:
        """Bare newline keeps the firmware idle timer from firing (~10 s)."""
        while self._running:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            if self._handshake == "ready" and self._writer is not None:
                try:
                    self._writer.write(b"\n")
                    await self._writer.drain()
                except (OSError, asyncio.CancelledError):
                    pass

    def _notify_connection(self, connected: bool) -> None:
        if self.on_connection is not None:
            try:
                self.on_connection(connected)
            except Exception:  # never let UI callbacks break the socket loop
                _LOGGER.exception("on_connection callback failed")

    # -- incoming lines --------------------------------------------------

    def _handle_line(self, line: str) -> None:
        """Route one wire line: handshake, MCu capture, alarms, telemetry."""
        self.recent_lines.append((time.monotonic(), line))

        if self._mcu_capture is not None and not line.startswith("<"):
            cleaned = "".join(c for c in line if 32 <= ord(c) <= 126).strip()
            if len(cleaned) > 2:
                self._mcu_capture.append(cleaned)

        if self._handshake == "identify" and line.startswith("XML="):
            self._handshake = "rich"
            self._write_line(CMD_RICH)
            return
        if self._handshake == "rich" and line.lstrip().startswith("{"):
            self._handshake = "ready"
            _LOGGER.info("ITO rich stream ready (10 Hz)")
            return
        if self._handshake != "ready":
            return

        token = parsers.parse_alarm_token(line)
        if token is not None and self.on_alarm is not None:
            try:
                self.on_alarm(token, parsers.ALARM_REASONS[token])
            except Exception:
                _LOGGER.exception("on_alarm callback failed")

        if "<" in line and ">" in line:
            sample = parsers.parse_lcd_frame(line)
            if sample is not None:
                self.state = sample["state"]
                self._emit(sample, is_rich=False)
                return

        ascii_line = "".join(c for c in line if 32 <= ord(c) <= 126).strip()
        if ascii_line.startswith("{") and ascii_line.endswith("}"):
            sample = parsers.parse_rich_frame(ascii_line)
            if sample is not None:
                self.state = sample["state"]
                self._emit(sample, is_rich=True)

    def _emit(self, sample: dict, is_rich: bool) -> None:
        if self.on_telemetry is not None:
            try:
                self.on_telemetry(sample, is_rich)
            except Exception:
                _LOGGER.exception("on_telemetry callback failed")

    # -- outgoing primitives ----------------------------------------------

    def _write_line(self, text: str) -> bool:
        """Send one command line; False when not connected."""
        if self._writer is None:
            _LOGGER.warning("Cannot send %r: ITO not connected", text)
            return False
        if not text.endswith("\n"):
            text += "\n"
        try:
            self._writer.write(text.encode("latin-1"))
            asyncio.get_running_loop().call_soon(
                lambda w=self._writer: asyncio.ensure_future(_drain(w))
            )
        except (OSError, RuntimeError):
            _LOGGER.warning("Send failed (not connected)")
            return False
        return True

    def _write_raw(self, payload: str) -> bool:
        """Send raw bytes without newline handling (nav keys)."""
        if self._writer is None:
            return False
        try:
            self._writer.write(payload.encode("latin-1"))
            asyncio.get_running_loop().call_soon(
                lambda w=self._writer: asyncio.ensure_future(_drain(w))
            )
        except (OSError, RuntimeError):
            return False
        return True

    def send_command(self, command: str) -> bool:
        """Short single-shot command (no lock; completes in milliseconds)."""
        return self._write_line(command)

    async def press_button(self, byte: str) -> bool:
        """Press + release a virtual button with the 30 ms firmware gap."""
        if not self._write_raw("MC" + byte + "\r"):
            return False
        await asyncio.sleep(PRESS_RELEASE_DELAY)
        return self._write_raw("MC" + BTN_RELEASE + "\r")

    # -- Phase 1 operations -------------------------------------------------

    async def start_shot(self) -> None:
        """Robust start: wake from STANDBY, leave MENU, fire Shortcut[1].

        Ports proxy.js startShot(): MCa up to 3x (600 ms apart, re-arm MCr
        after each wake because MCa drops the rich stream), then CANCEL up
        to 4x out of MENU, then MC\\x05 press. State checks gate each step;
        when already IDLE the shot fires immediately.
        """
        for _ in range(START_WAKE_RETRIES):
            if self.state != "STANDBY":
                break
            _LOGGER.info("Start: STANDBY -> MCa wake")
            self.send_command(CMD_WAKE)
            await asyncio.sleep(0.2)
            self._write_raw(CMD_RICH)
            await asyncio.sleep(START_WAKE_WAIT)
        for _ in range(START_MENU_EXITS):
            if self.state != "MENU":
                break
            _LOGGER.info("Start: MENU -> CANCEL")
            await self.press_button(BTN_CANCEL)
            await asyncio.sleep(START_STEP_WAIT)
        _LOGGER.info("Start: firing Shortcut[1] (state=%s)", self.state)
        await self.press_button(BTN_SHORTCUT_1)

    async def stop_shot(self) -> None:
        """Stop a running shot (OK button, proxy.js stopShot)."""
        await self.press_button(BTN_OK)

    async def adjust_setpoint(self, steps: int) -> None:
        """Nudge the setpoint by steps x 0.1 C (firmware is relative-only)."""
        command = "MC+\r" if steps > 0 else "MC-\r"
        for _ in range(abs(steps)):
            self.send_command(command)
            await asyncio.sleep(0.05)

    async def dump_setup(self) -> list[str]:
        """Capture one MCu dump under the long-op lock; re-arm rich stream."""
        async with self.lock:
            captured: list[str] = []
            self._mcu_capture = captured
            try:
                self._write_raw(CMD_DUMP_SETUP)
                await asyncio.sleep(MCU_CAPTURE_TIMEOUT)
                return list(captured)
            finally:
                self._mcu_capture = None
                self._write_raw(CMD_RICH)


async def _drain(writer: asyncio.StreamWriter) -> None:
    try:
        await writer.drain()
    except (OSError, asyncio.CancelledError):
        pass
