"""Fake ITO Leva! TCP device for HA integration tests.

Speaks the subset of the MC protocol the integration uses. Frames are built
with the vendor Status-Monitor field positions (1-indexed 'at' in the XML),
which is what the real firmware sends inside `{ ... }`.
"""

from __future__ import annotations

import asyncio
import contextlib


def build_rich(
    *,
    marker: str = "-",
    pid_type: str = "PID",
    temp: float = 93.5,
    setpoint: float = 94.0,
    pid_output: int = 4,
    pressure: float = 0.2,
    volume: int = 0,
    ticks: int = 0,
    weight: float | None = None,
    flow: float | None = None,
    profile: str = "TestProf",
) -> str:
    body = ["?"] * 287
    body[0] = " "
    body[1] = marker           # proxy/HA marker (frame pos 3)
    body[3:6] = list(pid_type)
    body[6:12] = list(f"{temp:06.2f}")
    body[12:17] = list(f"{setpoint:05.1f}")
    body[17:20] = list(f"{pid_output:03d}")      # Pos 19-21 (vendor at=18)
    body[151:154] = list(f"{volume:03d}")
    body[154:159] = list(f"{ticks:05d}")
    body[173:179] = list(f"{temp:06.2f}")       # Pos 175-180
    body[196:201] = list(f"{pressure:05.2f}")   # Pos 198-202
    body[201:206] = list(f"{max(0.0, pressure - 0.2):05.2f}")
    body[206:211] = list(f"{pressure + 0.2:05.2f}")
    if weight is not None:
        body[225:230] = list(f"{weight:05.1f}")
    if flow is not None:
        body[230:235] = list(f"{flow:05.2f}")
    body[235:244] = list(profile.ljust(9)[:9])
    body[271:280] = list(profile.ljust(9)[:9])
    return "{" + "".join(body) + " ~ 42}"


def build_lcd(row1: str, row2: str, row3: str, row4: str, prefix: str = "102") -> str:
    return (
        "<"
        + prefix
        + row1.ljust(16)[:16]
        + row2.ljust(16)[:16]
        + row3.ljust(16)[:16]
        + row4.ljust(16)[:16]
        + ">"
    )


class FakeIto:
    def __init__(self) -> None:
        self.port: int = 0
        self.commands: list[bytes] = []
        self.awake = True
        self.streaming = False
        self.lcd_mode = False
        self.shot = False
        self.flush = False
        self.block_shot = False
        self.setpoint = 94.0
        self.temp = 93.5
        self.pid_output = 4
        # Size of one MC+/MC- press. Real firmware exposes this via MC# and the
        # vendor "increment" field (0.1, 1.0 or 10); tests cover all of them.
        self.increment = 0.1
        # Simulates a program override (e.g. Ramp&Soak warm-up): the firmware
        # refuses setpoint keys while the override is active.
        self.refuse_setpoint = False
        self._ticks = 0
        self._volume = 0
        self._weight = 0.0
        self._writers: list[asyncio.StreamWriter] = []
        self._tasks: list[asyncio.Task] = []
        self._server: asyncio.AbstractServer | None = None
        self.frames_sent = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for writer in self._writers:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # -- device behaviour ---------------------------------------------------
    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writers.append(writer)
        writer.write(b"XML=leva!-FAKE\r\n")
        writer.write(b"$id: leva! (3.3)\r\n")
        await writer.drain()
        buf = ""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buf += chunk.decode("latin-1")
                buf = await self._process(buf, writer)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _process(self, buf: str, writer: asyncio.StreamWriter) -> str:
        while True:
            indexes = [i for i in (buf.find("\r"), buf.find("\n")) if i >= 0]
            if not indexes:
                return buf
            idx = min(indexes)
            line, buf = buf[:idx], buf[idx + 1 :]
            if line:
                await self._command(line, writer)

    async def _command(self, line: str, writer: asyncio.StreamWriter) -> None:
        self.commands.append(line.encode("latin-1"))
        if line == "MCE":
            self.awake = False
            self.lcd_mode = False
            return
        if line == "MCe":
            self.awake = True
            self.streaming = True
            self.setpoint = 81.3  # eco lowers the target, stays awake
            self._ensure_stream()
            return
        if line in ("MCcSTANDBY", "MCcECO"):
            # Legacy long forms are ignored by the real firmware.
            return
        if line.startswith("MCc"):
            if line == "MCcDOSE":
                self.awake = True
                self.streaming = True
                if not self.block_shot:
                    self.shot = True
                self._ensure_stream()
            return
        if line == "MC?":
            writer.write(b"XML=leva!-FAKE\r\n")
            await writer.drain()
        elif line == "MCr":
            self.streaming = True
            self.lcd_mode = False
            self._ensure_stream()
        elif line == "MCa":
            self.awake = True
            self.streaming = True
            self.lcd_mode = False
            self.setpoint = 94.0
            self._ensure_stream()
        elif line == "MC+":
            if not self.refuse_setpoint:
                self.setpoint = round(
                    min(100.0, self.setpoint + self.increment), 3
                )
        elif line == "MC-":
            if not self.refuse_setpoint:
                self.setpoint = round(
                    max(80.0, self.setpoint - self.increment), 3
                )
        elif line == "MC@":
            self.lcd_mode = True
            self._ensure_stream()
        elif line == "MCu":
            await self._send_setup(writer)
        elif line == "MC\x05":
            self.awake = True
            self.streaming = True
            self.flush = True
            self._ensure_stream()
        elif line == "MC\x03":
            self.shot = False

    def _ensure_stream(self) -> None:
        if not any(t.get_name() == "ito_stream" for t in self._tasks):
            self._tasks.append(asyncio.create_task(self._stream(), name="ito_stream"))

    async def _stream(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            if not self.awake and not self.shot:
                frame = "{" + "?" * 287 + " ~ 42}"
            else:
                if self.shot:
                    self._ticks += 10
                    self._volume += 1
                    self._weight += 0.4
                frame = build_rich(
                    marker="*" if self.shot else "-",
                    ticks=self._ticks,
                    volume=self._volume,
                    weight=self._weight if self._weight else None,
                    flow=2.4 if self.shot else None,
                    pressure=9.0 if self.shot else 0.2,
                    temp=self.temp,
                    setpoint=self.setpoint,
                    pid_output=self.pid_output,
                )
            for writer in list(self._writers):
                with contextlib.suppress(Exception):
                    if self.lcd_mode:
                        if self.shot:
                            lcd = build_lcd(
                                "WARM-UP         ",
                                f"1: {self.temp:.1f}\xdf       ",
                                "      100% 93.6\xdf",
                                " 9.0bR=2.4 25s   ",
                            )
                        else:
                            lcd = build_lcd(
                                "WARM-UP         ",
                                f"1: {self.temp:.1f}\xdf       ",
                                "      000% 93.6\xdf",
                                " 0.2b-  0s   0ml",
                            )
                        payload = lcd
                    else:
                        payload = frame
                    writer.write(payload.encode("latin-1") + b"\r\n")
                    await writer.drain()
            self.frames_sent += 1

    async def _send_setup(self, writer: asyncio.StreamWriter) -> None:
        for line in (
            "PID 1...",
            "| Control     PID",
            "| Setpoint    94.2",
            "| Signal ERR>1.0°",
            "| Eco       82.3°",
            "| Ramp&Soak...",
            "| | Warm-Up...",
            "| | | 1...",
            "| | | | Target   110.0°",
            "| | | 2...",
            "| | | | Target      PID",
            "Sensors...",
            "| Temperature...",
            "| | Type   NTC",
        ):
            writer.write((line + "\r\n").encode("latin-1"))
        await writer.drain()

    async def alarm(self, token: str) -> None:
        for writer in list(self._writers):
            with contextlib.suppress(Exception):
                writer.write(f"{token}\r\n".encode("latin-1"))
                await writer.drain()

    def sent(self, needle: bytes) -> bool:
        return any(needle in cmd for cmd in self.commands)
