"""Pure data models (no HA imports, unit-testable).

Profiles/doses are Phase 2 scope; they live here so Phase 1 code already
uses the right shapes (active dose/profile name as read-only text).
Setup sensors are Phase 1 read-only; write-back is Phase 3.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Step:
    """Single PI/shot step. bar == 0 with time > 0 is a pause (firmware rule)."""

    time: float
    bar: float
    unit: Optional[str] = None  # 'flow' steps are display-only, sent as bar=0


@dataclass
class ProfileSlot:
    """Firmware profile slot 0-3. Phase 2 only (editor + upload)."""

    slot: int
    name: str  # up to 9 chars on the firmware
    pi: list["Step"] = field(default_factory=list)
    shot: list["Step"] = field(default_factory=list)
    pi_enabled: bool = False
    shot_enabled: bool = False
    flood_enabled: bool = False
    flood_pressure: Optional[float] = None  # bar
    flood_pump_pwr: Optional[int] = None  # %
    track_flow: Optional[float] = None  # ml/s threshold
    track_time: Optional[int] = None  # s
    end: Optional[str] = None  # STOP | SUSTAIN | FULL POWER
    pid1: Optional[float] = None
    pid2: Optional[float] = None
    cue: Optional[float] = None
    pi_repeat: Optional[int] = None
    pi_wait: Optional[float] = None
    resize_pi: Optional[float] = None
    resize_shot: Optional[float] = None
    execute_pi: Optional[bool] = None
    execute_shot: Optional[bool] = None
    drop_ends_pi: Optional[bool] = None
    pi_first_drop_weight: Optional[float] = None


@dataclass
class DosePreset:
    """Dose preset. Phase 2 only; Phase 1 shows the active dose read-only."""

    key: str
    label: str
    method: str  # weight | time | volume
    quantity: float = 0.0
    profile_idx: int = 0
    ignore_flood: bool = False
    ignore_pi: bool = False
    pump_pwr: float = 0.0


@dataclass
class Telemetry:
    """One merged telemetry sample (rich frame preferred, LCD fallback)."""

    state: str = "UNKNOWN"
    pressure: Optional[float] = None  # bar, group head
    flow: Optional[float] = None  # g/s, derived from scale weight deltas
    flow_ml: Optional[float] = None  # ml/s, from flow meter (R= field)
    volume: Optional[float] = None  # ml
    weight: Optional[float] = None  # g, from scale (Waage)
    temp: Optional[float] = None  # C, group temp
    setpoint: Optional[float] = None  # C target (LCD only; rich has no setpoint)
    timer: Optional[float] = None  # s, shot elapsed
    shot_running: bool = False
    active_profile: Optional[str] = None
    alarm: Optional[str] = None  # last [TOKEN], e.g. REFILL, DSCALE
    raw: dict[str, Any] = field(default_factory=dict)
