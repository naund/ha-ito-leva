#!/usr/bin/env python3
"""Record ITO Leva! rich frames for pressure-control (PI) tuning.

Read-only. The only bytes sent are the handshake (`\\nMC?\\r`, `MCr\\r`), a bare
keepalive newline every 7 s, and - only with `--pressure-test` - the read-only
`MCb` dump command. No setpoint, button or program command is ever sent.

Why this exists
---------------
The pump pressure controller is a PI controller with bias (firmware manual
p.109):  output[deg] = bias + K*error + sum(I*error), cycle 200 ms, with
separate parameter sets below/above 2.5 bar (and K>SP / K<SP above 2.5 bar).
Every rich frame of a pressure-profiled shot carries the same internals the
Status Monitor plots: pressure, pressure setpoint, phase, phase bias, phase P,
phase I. This tool writes them to CSV so K and I can be tuned offline.

Usage
-----
    # The module accepts one client: switch switch.ito_leva_connection off first.
    python3 tools/record_shot.py --out tuning/
    # ... pull shots; each shot becomes tuning/shot_001.csv
    # Ctrl-C ends the session (tuning/session.csv keeps every frame).

    # Static pump characteristic (phase -> pressure, no flow): run the
    # firmware's pressure test first (manual p.50), then:
    python3 tools/record_shot.py --pressure-test --out tuning/

Frame rate: the rich stream runs at ~4 Hz (250 ms), i.e. slightly slower than
the 200 ms control cycle. Use it for envelopes (overshoot, settling, phase
oscillation), not for loop-internal dynamics.

Sanity check built in: the controller output must equal bias + P + I, so
`identity_err` in the summary shows immediately whether the field mapping is
right for this firmware build.
"""

from __future__ import annotations

import argparse
import csv
import re
import socket
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PKG = types.ModuleType("ito_leva")
_PKG.__path__ = [str(ROOT / "custom_components" / "ito_leva")]
sys.modules["ito_leva"] = _PKG

from ito_leva import parsers  # noqa: E402  (needs the package stub above)

COLUMNS = [
    "t_unix",
    "t_rel",
    "shot_running",
    "state",
    "temp",
    "setpoint",
    "pid_output",
    "pressure",
    "pressure_sp",
    "band_low",
    "band_high",
    "phase",
    "phase_bias",
    "phase_p",
    "phase_i",
    "flow_ml",
    "volume",
    "weight",
    "ticks",
    "timer",
    "profile",
]

# Shot boundaries: '*' starts a shot, the programme is over when no running
# frame arrived for SHOT_END_GRACE seconds (the pump stops before the timer).
SHOT_END_GRACE = 2.0


class RichLink:
    """Minimal MC-protocol reader: handshake, keepalive, parsed frames."""

    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.create_connection((host, port), timeout=8)
        self.sock.settimeout(0.2)
        self._buf = b""
        self._handshake = False
        self._next_keepalive = time.monotonic() + 7.0
        self.sock.sendall(b"\nMC?\r")

    def frames(self) -> list[tuple[float, dict]]:
        """Read whatever arrived; returns (monotonic_ts, sample) pairs."""
        try:
            chunk = self.sock.recv(8192)
        except (socket.timeout, TimeoutError):
            chunk = b""
        if chunk:
            self._buf += chunk
        if not self._handshake and b"XML=" in self._buf:
            self._handshake = True
            self.sock.sendall(b"MCr\r")
        *lines, self._buf = self._buf.split(b"\n")

        out: list[tuple[float, dict]] = []
        now = time.monotonic()
        for raw in lines:
            line = raw.decode("latin-1").strip("\r")
            if not line.startswith("{") or not line.rstrip().endswith("}"):
                continue
            sample = parsers.parse_rich_frame(line)
            if sample is not None:
                out.append((now, sample))
        if now > self._next_keepalive:
            self.sock.sendall(b"\n")
            self._next_keepalive = now + 7.0
        return out

    def send(self, command: str) -> None:
        self.sock.sendall(command.encode() + b"\r")

    def close(self) -> None:
        try:
            self.sock.sendall(b"MCr\r")
        finally:
            self.sock.close()


def _row(ts: float, t0: float, sample: dict) -> dict:
    return {
        "t_unix": f"{time.time():.3f}",
        "t_rel": f"{ts - t0:.3f}",
        "shot_running": int(bool(sample.get("shot_running"))),
        "state": sample.get("state"),
        "temp": sample.get("temp"),
        "setpoint": sample.get("setpoint"),
        "pid_output": sample.get("pid_output"),
        "pressure": sample.get("pressure"),
        "pressure_sp": sample.get("pressure_sp"),
        "band_low": sample.get("pressure_low"),
        "band_high": sample.get("pressure_high"),
        "phase": sample.get("phase"),
        "phase_bias": sample.get("phase_bias"),
        "phase_p": sample.get("phase_p"),
        "phase_i": sample.get("phase_i"),
        "flow_ml": sample.get("flow_ml"),
        "volume": sample.get("volume"),
        "weight": sample.get("weight"),
        "ticks": sample.get("ticks"),
        "timer": sample.get("timer"),
        "profile": sample.get("active_profile_name"),
    }


def _stats(values: list[float]) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return mean, var ** 0.5, max(values)


def _plateaus(rows: list[dict], min_frames: int = 4) -> list[list[dict]]:
    """Split a shot into constant pressure-setpoint segments (profile steps)."""
    segments: list[list[dict]] = []
    current: list[dict] = []
    last_sp: float | None = None
    for row in rows:
        sp = row["pressure_sp"]
        if not isinstance(sp, float):
            continue
        key = round(sp, 1)
        if last_sp is not None and abs(key - last_sp) > 0.05:
            if len(current) >= min_frames:
                segments.append(current)
            current = []
        current.append(row)
        last_sp = key
    if len(current) >= min_frames:
        segments.append(current)
    return segments


def summarize(rows: list[dict], name: str) -> None:
    """Print the numbers a PI tuning decision needs."""
    plateau = rows[int(len(rows) * 0.5) :] if len(rows) > 4 else rows
    pressure = [r["pressure"] for r in rows if isinstance(r["pressure"], float)]
    p_plateau = [r["pressure"] for r in plateau if isinstance(r["pressure"], float)]
    sp = [r["pressure_sp"] for r in plateau if isinstance(r["pressure_sp"], float)]
    phase = [r["phase"] for r in plateau if isinstance(r["phase"], float)]
    flow = [r["flow_ml"] for r in plateau if isinstance(r["flow_ml"], float)]
    identity = [
        abs(r["phase"] - (r["phase_bias"] + r["phase_p"] + r["phase_i"]))
        for r in plateau
        if all(
            isinstance(r[k], float)
            for k in ("phase", "phase_bias", "phase_p", "phase_i")
        )
    ]

    print(f"\n--- {name}: {len(rows)} frames, {rows[-1]['t_rel']} s ---")
    if not pressure:
        print("  no pressure data")
        return
    mean_p, sd_p, max_p = _stats(p_plateau or pressure)
    print(f"  pressure plateau : {mean_p:.2f} bar  (sd {sd_p:.2f})")
    print(f"  pressure peak    : {max_p:.2f} bar")
    if sp:
        mean_sp = sum(sp) / len(sp)
        print(f"  target (SP)      : {mean_sp:.2f} bar")
        print(f"  steady error     : {mean_p - mean_sp:+.2f} bar")
        print(f"  overshoot        : {max_p - mean_sp:+.2f} bar")
    if phase:
        mean_ph, sd_ph, _ = _stats(phase)
        print(f"  phase            : {mean_ph:.0f} deg  (sd {sd_ph:.1f})")
        print("                     high sd = K too strong (phase hunting)")
    if flow:
        print(f"  flow             : {min(flow):.1f}-{max(flow):.1f} ml/s"
              + ("  CHOKED" if min(flow) <= 0.0 else ""))
    if identity:
        print(f"  identity |phase-(bias+P+I)|: max {max(identity):.1f} deg"
              "  (should be ~0; large = mapping/rounding off)")

    # Per profile step: one row per constant pressure target, which is what the
    # manual asks you to compare when tuning K and I.
    segments = _plateaus(rows)
    if len(segments) > 1:
        print("  per step:")
        for seg in segments:
            seg_p = [r["pressure"] for r in seg if isinstance(r["pressure"], float)]
            seg_sp = [r["pressure_sp"] for r in seg if isinstance(r["pressure_sp"], float)]
            seg_ph = [r["phase"] for r in seg if isinstance(r["phase"], float)]
            if not seg_p or not seg_sp:
                continue
            seg_mean, seg_sd, _ = _stats(seg_p)
            _, ph_sd, _ = _stats(seg_ph)
            print(
                f"    SP {sum(seg_sp) / len(seg_sp):4.1f} bar"
                f" -> {seg_mean:4.2f} +/-{seg_sd:.2f} bar"
                f"  (err {seg_mean - sum(seg_sp) / len(seg_sp):+.2f},"
                f" phase sd {ph_sd:.1f} deg)"
            )


def parse_pressure_test(lines: list[str]) -> list[tuple[float, float]]:
    """Extract (phase, pressure) pairs from an MCb dump."""
    pairs: list[tuple[float, float]] = []
    for line in lines:
        numbers = re.findall(r"-?\d+(?:[.,]\d+)?", line)
        if len(numbers) == 2:
            phase, pressure = (float(n.replace(",", ".")) for n in numbers)
            if 0 <= phase <= 180 and 0 <= pressure <= 30:
                pairs.append((phase, pressure))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record ITO Leva! rich frames for pressure PI tuning (read-only)"
    )
    parser.add_argument("--host", default="192.168.178.21")
    parser.add_argument("--port", type=int, default=23)
    parser.add_argument("--out", default="tuning", help="output directory")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="stop after N seconds (0 = until Ctrl-C)")
    parser.add_argument("--pressure-test", action="store_true",
                        help="capture the MCb static phase/pressure dump and exit")
    parser.add_argument("--pt-duration", type=float, default=25.0,
                        help="seconds to collect the MCb dump")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    link = RichLink(args.host, args.port)
    print(f"connected to {args.host}:{args.port}, writing to {out_dir}/")

    try:
        if args.pressure_test:
            # Re-arm rich reporting first so the module answers, then dump.
            link.send("MCr")
            time.sleep(0.5)
            link.send("MCb")  # read-only: "Dump pressure test data"
            deadline = time.monotonic() + args.pt_duration
            lines: list[str] = []
            while time.monotonic() < deadline:
                try:
                    chunk = link.sock.recv(8192)
                except (socket.timeout, TimeoutError):
                    chunk = b""
                if chunk:
                    link._buf += chunk
                *raw_lines, link._buf = link._buf.split(b"\n")
                lines.extend(
                    ln.decode("latin-1").strip("\r")
                    for ln in raw_lines
                    if ln.strip() and not ln.startswith(b"{")
                )
                if time.monotonic() > link._next_keepalive:
                    link.sock.sendall(b"\n")
                    link._next_keepalive = time.monotonic() + 7.0
            path = out_dir / "pressure_test.txt"
            path.write_text("\n".join(lines), encoding="utf-8")
            pairs = parse_pressure_test(lines)
            print(f"MCb dump: {len(lines)} lines -> {path}")
            if pairs:
                print(f"phase/pressure pairs: {len(pairs)}")
                for phase, pressure in pairs[:: max(1, len(pairs) // 12)]:
                    print(f"  {phase:6.1f} deg -> {pressure:5.2f} bar")
            else:
                print("no [phase, pressure] pairs recognised - inspect the file")
            return 0

        session = open(out_dir / "session.csv", "w", newline="", encoding="utf-8")
        writer = csv.DictWriter(session, fieldnames=COLUMNS)
        writer.writeheader()

        shots: list[tuple[str, list[dict]]] = []
        current: list[dict] = []
        last_running = 0.0
        t0 = time.monotonic()
        deadline = t0 + args.duration if args.duration else None
        seen = 0

        print("waiting for shots (Ctrl-C to stop) ...")
        try:
            while deadline is None or time.monotonic() < deadline:
                for ts, sample in link.frames():
                    t0 = t0 or ts
                    row = _row(ts, t0, sample)
                    writer.writerow(row)
                    session.flush()
                    seen += 1
                    if sample.get("shot_running"):
                        last_running = ts
                        current.append(row)
                    elif current:
                        current.append(row)
                        if ts - last_running > SHOT_END_GRACE:
                            name = f"shot_{len(shots) + 1:03d}"
                            with open(out_dir / f"{name}.csv", "w", newline="",
                                      encoding="utf-8") as fh:
                                shot_writer = csv.DictWriter(fh, fieldnames=COLUMNS)
                                shot_writer.writeheader()
                                shot_writer.writerows(current)
                            summarize(current, name)
                            shots.append((name, current))
                            current = []
        except KeyboardInterrupt:
            print("\nstopped")
        finally:
            session.close()
            if current:
                name = f"shot_{len(shots) + 1:03d}"
                with open(out_dir / f"{name}.csv", "w", newline="",
                          encoding="utf-8") as fh:
                    shot_writer = csv.DictWriter(fh, fieldnames=COLUMNS)
                    shot_writer.writeheader()
                    shot_writer.writerows(current)
                summarize(current, name)

        elapsed = max(1e-6, time.monotonic() - t0)
        print(f"\n{seen} frames in {elapsed:.1f} s = {seen / elapsed:.1f} Hz, "
              f"{len(shots)} shot(s) captured")
        return 0
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
