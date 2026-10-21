"""VantaClock — explicit time semantics for every timestamp in VantaFlight.

A camera's device clock, PX4's boot clock, the host's monotonic clock, UTC
wall time, Gazebo simulation time and a video file's media timeline all run
independently. A ``TimeStamp`` therefore always names the clock it was read
from, and two timestamps from different clocks are never subtracted or
compared: that raises ``MixedClockDomainError`` instead of silently
producing a wrong age.

Host monotonic time is the canonical scheduling clock. UTC wall time is for
records, file names and cross-machine correlation, never for deadlines (it
can jump when NTP corrects it).

A foreign clock is mapped onto host monotonic time by a per-source linear
model fitted online from (source time, host receive time) pairs::

    t_host ≈ a · t_source + b

``ClockSync`` keeps residual statistics, rejects single outliers, detects
clock resets (e.g. a vehicle reboot) and attaches the uncertainty of every
conversion to the converted ``TimeStamp``.

One-way timing cannot observe the minimum transport latency between capture
and receipt, so a converted capture time is biased late by that latency
unless it is supplied (``nominal_latency_ns``, e.g. from a CameraProfile).
That bias is reported, not hidden.
"""
from __future__ import annotations

import enum
import itertools
import math
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable

NS_PER_S = 1_000_000_000


class ClockDomain(str, enum.Enum):
    HOST_MONOTONIC = "HOST_MONOTONIC"
    UTC_WALL = "UTC_WALL"
    VEHICLE_BOOT = "VEHICLE_BOOT"
    CAMERA_DEVICE = "CAMERA_DEVICE"
    SIMULATION = "SIMULATION"
    MEDIA_TIMELINE = "MEDIA_TIMELINE"
    REMOTE_AGENT = "REMOTE_AGENT"


#: Domains that need a ClockSync (keyed by clock_id) to reach host time.
FOREIGN_DOMAINS = frozenset(
    {
        ClockDomain.VEHICLE_BOOT,
        ClockDomain.CAMERA_DEVICE,
        ClockDomain.SIMULATION,
        ClockDomain.MEDIA_TIMELINE,
        ClockDomain.REMOTE_AGENT,
    }
)


class MixedClockDomainError(TypeError):
    """Arithmetic or comparison between timestamps from different clocks."""


class ClockConversionError(ValueError):
    """A timestamp cannot be mapped to the requested clock (no sync yet)."""


def _rss(*values: int | None) -> int | None:
    """Root-sum-square of independent uncertainties (None = exact)."""
    present = [v for v in values if v]
    if not present:
        return None
    return int(math.ceil(math.sqrt(sum(float(v) * float(v) for v in present))))


@dataclass(frozen=True, slots=True)
class Elapsed:
    """A duration between two timestamps of the same clock."""

    value_ns: int
    uncertainty_ns: int | None = None

    @property
    def seconds(self) -> float:
        return self.value_ns / NS_PER_S

    @property
    def ms(self) -> float:
        return self.value_ns / 1_000_000

    @property
    def uncertainty_s(self) -> float | None:
        return None if self.uncertainty_ns is None else self.uncertainty_ns / NS_PER_S


@dataclass(frozen=True, slots=True)
class TimeStamp:
    """An instant on one named clock.

    ``uncertainty_ns`` is None for a value read directly from its own clock
    and set for values produced by a conversion. ``clock_id`` tells apart
    clocks that share a domain (two cameras each have their own device clock).
    """

    value_ns: int
    domain: ClockDomain
    uncertainty_ns: int | None = None
    clock_id: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.value_ns, bool) or not isinstance(self.value_ns, int):
            raise TypeError("value_ns must be an int number of nanoseconds")
        object.__setattr__(self, "domain", ClockDomain(self.domain))
        if self.uncertainty_ns is not None:
            if isinstance(self.uncertainty_ns, bool) or not isinstance(self.uncertainty_ns, int):
                raise TypeError("uncertainty_ns must be an int or None")
            if self.uncertainty_ns < 0:
                raise ValueError("uncertainty_ns cannot be negative")
        if not isinstance(self.clock_id, str):
            raise TypeError("clock_id must be a string")

    # -- construction -----------------------------------------------------------
    @classmethod
    def from_seconds(
        cls,
        seconds: float,
        domain: ClockDomain,
        *,
        uncertainty_s: float | None = None,
        clock_id: str = "",
    ) -> "TimeStamp":
        if not math.isfinite(seconds):
            raise ValueError("seconds must be finite")
        unc = None if uncertainty_s is None else int(math.ceil(uncertainty_s * NS_PER_S))
        return cls(int(round(seconds * NS_PER_S)), domain, unc, clock_id)

    # -- views ----------------------------------------------------------------------
    @property
    def seconds(self) -> float:
        return self.value_ns / NS_PER_S

    @property
    def uncertainty_s(self) -> float | None:
        return None if self.uncertainty_ns is None else self.uncertainty_ns / NS_PER_S

    @property
    def clock_key(self) -> tuple[ClockDomain, str]:
        return (self.domain, self.clock_id)

    def describe(self) -> str:
        return f"{self.domain.value}:{self.clock_id}" if self.clock_id else self.domain.value

    def same_clock(self, other: "TimeStamp") -> bool:
        return self.clock_key == other.clock_key

    def _require_same_clock(self, other: object) -> "TimeStamp":
        if not isinstance(other, TimeStamp):
            raise TypeError(f"expected a TimeStamp, got {type(other).__name__}")
        if not self.same_clock(other):
            raise MixedClockDomainError(
                f"cannot combine {self.describe()} with {other.describe()}; "
                "convert one of them with VantaClock first"
            )
        return other

    # -- arithmetic ---------------------------------------------------------------------
    def __sub__(self, other: "TimeStamp") -> Elapsed:
        other = self._require_same_clock(other)
        return Elapsed(self.value_ns - other.value_ns, _rss(self.uncertainty_ns, other.uncertainty_ns))

    def plus_ns(self, delta_ns: int) -> "TimeStamp":
        return TimeStamp(self.value_ns + int(delta_ns), self.domain, self.uncertainty_ns, self.clock_id)

    def plus_s(self, delta_s: float) -> "TimeStamp":
        return self.plus_ns(int(round(delta_s * NS_PER_S)))

    def __lt__(self, other: "TimeStamp") -> bool:
        return self.value_ns < self._require_same_clock(other).value_ns

    def __le__(self, other: "TimeStamp") -> bool:
        return self.value_ns <= self._require_same_clock(other).value_ns

    def __gt__(self, other: "TimeStamp") -> bool:
        return self.value_ns > self._require_same_clock(other).value_ns

    def __ge__(self, other: "TimeStamp") -> bool:
        return self.value_ns >= self._require_same_clock(other).value_ns

    # -- serialization -------------------------------------------------------------------
    def to_dict(self) -> dict:
        out: dict = {"value_ns": self.value_ns, "domain": self.domain.value}
        if self.uncertainty_ns is not None:
            out["uncertainty_ns"] = self.uncertainty_ns
        if self.clock_id:
            out["clock_id"] = self.clock_id
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> "TimeStamp":
        return cls(
            int(raw["value_ns"]),
            ClockDomain(raw["domain"]),
            None if raw.get("uncertainty_ns") is None else int(raw["uncertainty_ns"]),
            str(raw.get("clock_id", "")),
        )


# -- clock synchronization ---------------------------------------------------------------
class SyncOutcome(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    OUTLIER = "OUTLIER"  # rejected; the fit is unchanged
    JUMP = "JUMP"  # the source clock was reset; the fit restarted


@dataclass(frozen=True)
class ClockSyncConfig:
    window: int = 256
    #: Conversions are refused until this many samples have been accepted:
    #: one or two samples say nothing about the transport jitter.
    min_samples_to_sync: int = 3
    #: Drift (a ≠ 1) is only fitted with enough samples spread over enough time.
    min_samples_for_drift: int = 8
    min_span_for_drift_s: float = 2.0
    #: A sample whose offset departs from the fit by more than this is either
    #: an outlier or, if it repeats consistently, a clock reset.
    jump_threshold_s: float = 0.25
    jump_confirm_samples: int = 3
    #: Fitted drift beyond this is implausible for a crystal; fall back to offset-only.
    max_drift_ppm: float = 1000.0
    #: Unmodelled drift assumed while drift is not fitted (uncertainty only).
    assumed_drift_ppm: float = 100.0

    def __post_init__(self) -> None:
        if (
            self.window < 4
            or self.min_samples_to_sync < 2
            or self.min_samples_for_drift < 3
            or self.jump_confirm_samples < 2
        ):
            raise ValueError("clock sync window/sample counts are too small")
        if self.jump_threshold_s <= 0 or self.min_span_for_drift_s <= 0:
            raise ValueError("clock sync thresholds must be positive")


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


class ClockSync:
    """Online linear map from one source clock to host monotonic time.

    Fit on offsets: ``d = t_host - t_source = k · x + c`` where x is source time
    (seconds, relative to the window start) and k = a - 1 is the drift. The
    host *receive* times include transport latency, so the mapping uses the
    lower envelope of the residuals (their 5th percentile): receive times can
    only be late, never early.
    """

    _ENVELOPE_Q = 0.05

    def __init__(
        self,
        domain: ClockDomain,
        clock_id: str = "",
        *,
        config: ClockSyncConfig | None = None,
        nominal_latency_ns: int = 0,
        latency_uncertainty_ns: int = 0,
    ) -> None:
        domain = ClockDomain(domain)
        if domain in (ClockDomain.HOST_MONOTONIC, ClockDomain.UTC_WALL):
            raise ValueError(f"{domain.value} does not need a ClockSync")
        if nominal_latency_ns < 0 or latency_uncertainty_ns < 0:
            raise ValueError("latencies cannot be negative")
        self.domain = domain
        self.clock_id = clock_id
        self.config = config or ClockSyncConfig()
        self.nominal_latency_ns = int(nominal_latency_ns)
        self.latency_uncertainty_ns = int(latency_uncertainty_ns)
        self._src: deque[int] = deque(maxlen=self.config.window)
        self._host: deque[int] = deque(maxlen=self.config.window)
        self._candidates: list[tuple[int, int]] = []
        self._lock = threading.Lock()
        self._total = 0
        self._outliers = 0
        self._jumps = 0
        self._last_host_ns: int | None = None
        self._fit: _Fit | None = None

    # -- observations ------------------------------------------------------------
    def observe(self, source: TimeStamp, host_receive: TimeStamp) -> SyncOutcome:
        if source.clock_key != (self.domain, self.clock_id):
            raise MixedClockDomainError(
                f"this sync maps {self.domain.value}:{self.clock_id}, not {source.describe()}"
            )
        if host_receive.domain is not ClockDomain.HOST_MONOTONIC:
            raise MixedClockDomainError("host_receive must be a HOST_MONOTONIC timestamp")
        with self._lock:
            self._total += 1
            self._last_host_ns = host_receive.value_ns
            src, host = source.value_ns, host_receive.value_ns
            if self._fit is None:
                self._accept(src, host)
                return SyncOutcome.ACCEPTED
            deviation_s = (host - self._fit.predict_receive_ns(src)) / NS_PER_S
            if abs(deviation_s) <= self.config.jump_threshold_s:
                self._candidates.clear()
                self._accept(src, host)
                return SyncOutcome.ACCEPTED
            self._candidates.append((src, host))
            if len(self._candidates) >= self.config.jump_confirm_samples and self._consistent(self._candidates):
                self._jumps += 1
                pending = list(self._candidates)
                self._candidates.clear()
                self._src.clear()
                self._host.clear()
                for s, h in pending:
                    self._src.append(s)
                    self._host.append(h)
                self._refit()
                return SyncOutcome.JUMP
            self._outliers += 1
            if len(self._candidates) >= self.config.jump_confirm_samples:
                # Inconsistent spikes: forget the oldest so a real jump can
                # still be confirmed by the next consistent run.
                self._candidates.pop(0)
            return SyncOutcome.OUTLIER

    def _consistent(self, pairs: list[tuple[int, int]]) -> bool:
        offsets = [(h - s) / NS_PER_S for s, h in pairs]
        return max(offsets) - min(offsets) <= self.config.jump_threshold_s

    def _accept(self, src: int, host: int) -> None:
        self._src.append(src)
        self._host.append(host)
        self._refit()

    def _refit(self) -> None:
        n = len(self._src)
        if n == 0:
            self._fit = None
            return
        x_ref = self._src[0]
        y_ref = self._host[0] - self._src[0]
        xs = [(s - x_ref) / NS_PER_S for s in self._src]
        ys = [((h - s) - y_ref) / NS_PER_S for s, h in zip(self._src, self._host)]
        cfg = self.config
        span = xs[-1] - xs[0] if n > 1 else 0.0
        x_mean = sum(xs) / n
        k = 0.0
        sxx = sum((x - x_mean) ** 2 for x in xs)
        if n >= cfg.min_samples_for_drift and span >= cfg.min_span_for_drift_s and sxx > 0:
            y_mean = sum(ys) / n
            k = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / sxx
            if abs(k) * 1e6 > cfg.max_drift_ppm:
                k = 0.0
        drift_fitted = k != 0.0
        if drift_fitted:
            c = sum(ys) / n - k * x_mean
        else:
            c = sum(ys) / n
        residuals = sorted(y - (k * x + c) for x, y in zip(xs, ys))
        envelope = _percentile(residuals, self._ENVELOPE_Q)
        median = _percentile(residuals, 0.5)
        spread = sorted(abs(r - median) for r in residuals)
        dof = n - (2 if drift_fitted else 1)
        s = math.sqrt(sum(r * r for r in residuals) / dof) if dof > 0 else 0.0
        self._fit = _Fit(
            x_ref_ns=x_ref,
            y_ref_ns=y_ref,
            k=k,
            c_s=c,
            envelope_s=envelope,
            drift_fitted=drift_fitted,
            n=n,
            x_mean_s=x_mean,
            sxx=sxx,
            resid_std_s=s,
            jitter_band_s=_percentile(residuals, 0.95) - envelope,
            envelope_bias_s=envelope - residuals[0],
            spread_p50_s=_percentile(spread, 0.50),
            spread_p95_s=_percentile(spread, 0.95),
            spread_p99_s=_percentile(spread, 0.99),
            last_x_s=xs[-1],
            assumed_drift=cfg.assumed_drift_ppm * 1e-6,
        )

    # -- conversion ------------------------------------------------------------
    @property
    def synced(self) -> bool:
        return self._fit is not None and self._fit.n >= self.config.min_samples_to_sync

    def _usable_fit(self, what: str) -> "_Fit":
        fit = self._fit
        if fit is None or fit.n < self.config.min_samples_to_sync:
            have = 0 if fit is None else fit.n
            raise ClockConversionError(
                f"clock sync for {what} has {have} sample(s); "
                f"{self.config.min_samples_to_sync} needed before converting"
            )
        return fit

    def to_host(self, source: TimeStamp) -> TimeStamp:
        if source.clock_key != (self.domain, self.clock_id):
            raise MixedClockDomainError(f"this sync cannot convert {source.describe()}")
        fit = self._usable_fit(source.describe())
        receive_ns = fit.envelope_receive_ns(source.value_ns)
        value = receive_ns - self.nominal_latency_ns
        unc = _rss(fit.uncertainty_ns(source.value_ns), self.latency_uncertainty_ns, source.uncertainty_ns)
        return TimeStamp(int(round(value)), ClockDomain.HOST_MONOTONIC, unc)

    def from_host(self, host: TimeStamp) -> TimeStamp:
        if host.domain is not ClockDomain.HOST_MONOTONIC:
            raise MixedClockDomainError("from_host expects a HOST_MONOTONIC timestamp")
        fit = self._usable_fit(f"{self.domain.value}:{self.clock_id}")
        # Invert t_host = t_src + y_ref + (k·x + c + env)·1e9 - latency for t_src.
        target = host.value_ns + self.nominal_latency_ns
        a = 1.0 + fit.k
        rhs = target - fit.y_ref_ns - (fit.c_s + fit.envelope_s) * NS_PER_S + fit.k * fit.x_ref_ns
        src = rhs / a
        unc = _rss(fit.uncertainty_ns(int(src)), self.latency_uncertainty_ns, host.uncertainty_ns)
        return TimeStamp(int(round(src)), self.domain, unc, self.clock_id)

    def stats(self) -> dict:
        fit = self._fit
        out = {
            "domain": self.domain.value,
            "clock_id": self.clock_id,
            "synced": self.synced,
            "samples_total": self._total,
            "samples_in_window": len(self._src),
            "outliers": self._outliers,
            "jumps": self._jumps,
            "nominal_latency_ms": round(self.nominal_latency_ns / 1e6, 3),
            # Without a known minimum transport latency, converted capture
            # times are late by that latency (one-way timing cannot see it).
            "latency_compensated": self.nominal_latency_ns > 0,
        }
        if fit is not None:
            out.update(
                {
                    # host - source at the newest sample (lower envelope)
                    "offset_s": round(
                        fit.y_ref_ns / NS_PER_S + fit.k * fit.last_x_s + fit.c_s + fit.envelope_s, 9
                    ),
                    # positive = the source clock runs fast relative to the host
                    "source_drift_ppm": round((1.0 / (1.0 + fit.k) - 1.0) * 1e6, 3),
                    "drift_fitted": fit.drift_fitted,
                    "residual_p50_ms": round(fit.spread_p50_s * 1e3, 3),
                    "residual_p95_ms": round(fit.spread_p95_s * 1e3, 3),
                    "residual_p99_ms": round(fit.spread_p99_s * 1e3, 3),
                    "jitter_band_ms": round(fit.jitter_band_s * 1e3, 3),
                    "conversion_uncertainty_ms": round(
                        (fit.uncertainty_ns(fit.x_ref_ns + int(fit.last_x_s * NS_PER_S)) or 0) / 1e6, 3
                    ),
                }
            )
        return out


@dataclass(frozen=True)
class _Fit:
    x_ref_ns: int
    y_ref_ns: int
    k: float
    c_s: float
    envelope_s: float
    drift_fitted: bool
    n: int
    x_mean_s: float
    sxx: float
    resid_std_s: float
    jitter_band_s: float
    envelope_bias_s: float
    spread_p50_s: float
    spread_p95_s: float
    spread_p99_s: float
    last_x_s: float
    assumed_drift: float

    def _offset_s(self, src_ns: int) -> float:
        x = (src_ns - self.x_ref_ns) / NS_PER_S
        return self.k * x + self.c_s

    def predict_receive_ns(self, src_ns: int) -> float:
        """Mean receive time (fit line, including mean latency)."""
        return src_ns + self.y_ref_ns + self._offset_s(src_ns) * NS_PER_S

    def envelope_receive_ns(self, src_ns: int) -> float:
        """Earliest plausible receive time (lower envelope)."""
        return self.predict_receive_ns(src_ns) + self.envelope_s * NS_PER_S

    #: Statistical terms are reported at ~3 sigma so the uncertainty works
    #: as a bound, not a 1-sigma hint.
    COVERAGE_K = 3.0

    def uncertainty_ns(self, src_ns: int) -> int:
        """Bound on the conversion error at ``src_ns`` (excluding the
        unobservable minimum transport latency, handled by nominal_latency)."""
        x = (src_ns - self.x_ref_ns) / NS_PER_S
        if self.drift_fitted and self.sxx > 0:
            se_line = self.resid_std_s * math.sqrt(1.0 / self.n + (x - self.x_mean_s) ** 2 / self.sxx)
            unmodelled = 0.0
        else:
            se_line = self.resid_std_s / math.sqrt(self.n)
            # Drift is not fitted yet: allow for a plausible crystal drift
            # accumulated since the middle of the window.
            unmodelled = self.assumed_drift * abs(x - self.x_mean_s)
        q = ClockSync._ENVELOPE_Q
        # Standard error of the residual quantile that defines the envelope.
        se_quantile = max(self.jitter_band_s, 0.0) * math.sqrt(q * (1.0 - q) / self.n)
        statistical = math.sqrt(se_line * se_line + se_quantile * se_quantile)
        # The q-quantile sits above the true fastest delivery; the observed
        # minimum residual tells by how much.
        bias = max(self.envelope_bias_s, 0.0)
        total = self.COVERAGE_K * statistical + bias + unmodelled
        return int(math.ceil(total * NS_PER_S)) + 1


# -- time sources --------------------------------------------------------------------
class FakeTimeSource:
    """Deterministic monotonic + wall time for tests and replays."""

    def __init__(self, monotonic_s: float = 1000.0, wall_s: float = 1_790_000_000.0) -> None:
        self._mono_ns = int(round(monotonic_s * NS_PER_S))
        self._wall_ns = int(round(wall_s * NS_PER_S))
        self._lock = threading.Lock()

    def monotonic_ns(self) -> int:
        return self._mono_ns

    def wall_ns(self) -> int:
        return self._wall_ns

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("a monotonic clock cannot go backwards")
        delta = int(round(seconds * NS_PER_S))
        with self._lock:
            self._mono_ns += delta
            self._wall_ns += delta

    def step_wall(self, seconds: float) -> None:
        """Jump the wall clock only (an NTP correction)."""
        with self._lock:
            self._wall_ns += int(round(seconds * NS_PER_S))


class VantaClock:
    """The process's clocks, their syncs, and safe conversions between them.

    ``VantaClock`` is also callable and returns host monotonic seconds, so it
    can be passed anywhere a legacy ``clock: Callable[[], float]`` is
    expected (RaceSession, AutonomyLoop, MissionRunner...).
    """

    def __init__(
        self,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        wall_ns: Callable[[], int] = time.time_ns,
        sync_config: ClockSyncConfig | None = None,
        fake: FakeTimeSource | None = None,
    ) -> None:
        self._mono = monotonic_ns
        self._wall = wall_ns
        self._fake = fake
        self._sync_config = sync_config or ClockSyncConfig()
        self._syncs: dict[tuple[ClockDomain, str], ClockSync] = {}
        self._lock = threading.Lock()
        self._anchor: tuple[int, int, int] = (0, 0, 0)
        self._reanchor()

    # -- constructors --------------------------------------------------------------
    @classmethod
    def fake(cls, monotonic_s: float = 1000.0, wall_s: float = 1_790_000_000.0) -> "VantaClock":
        source = FakeTimeSource(monotonic_s, wall_s)
        return cls(monotonic_ns=source.monotonic_ns, wall_ns=source.wall_ns, fake=source)

    @classmethod
    def from_seconds(cls, monotonic_s: Callable[[], float]) -> "VantaClock":
        """Wrap a legacy float-seconds monotonic clock (e.g. a test FakeClock).

        Wall time is derived from it: anchored to real UTC once, then advancing
        with the wrapped clock, so records stay ordered with simulated time.
        """
        if isinstance(monotonic_s, VantaClock):
            return monotonic_s
        start_mono = int(round(monotonic_s() * NS_PER_S))
        start_wall = time.time_ns()

        def mono() -> int:
            return int(round(monotonic_s() * NS_PER_S))

        def wall() -> int:
            return start_wall + (mono() - start_mono)

        return cls(monotonic_ns=mono, wall_ns=wall)

    # -- reading clocks --------------------------------------------------------------
    @property
    def fake_source(self) -> FakeTimeSource | None:
        return self._fake

    def advance(self, seconds: float) -> None:
        """Advance a fake clock (tests only)."""
        if self._fake is None:
            raise RuntimeError("only a fake VantaClock can be advanced")
        self._fake.advance(seconds)

    def __call__(self) -> float:
        return self._mono() / NS_PER_S

    def monotonic_s(self) -> float:
        return self._mono() / NS_PER_S

    def now(self, domain: ClockDomain = ClockDomain.HOST_MONOTONIC, clock_id: str = "") -> TimeStamp:
        domain = ClockDomain(domain)
        if domain is ClockDomain.HOST_MONOTONIC:
            return TimeStamp(self._mono(), domain)
        if domain is ClockDomain.UTC_WALL:
            self._reanchor()
            return TimeStamp(self._wall(), domain)
        sync = self._syncs.get((domain, clock_id))
        if sync is None:
            raise ClockConversionError(f"no clock sync registered for {domain.value}:{clock_id}")
        return sync.from_host(self.now())

    def wall_s(self) -> float:
        return self.now(ClockDomain.UTC_WALL).seconds

    def _reanchor(self) -> None:
        before = self._mono()
        wall = self._wall()
        after = self._mono()
        # The anchor pairs a wall reading with the monotonic midpoint; the
        # half-gap bounds how far apart the two reads could be.
        self._anchor = ((before + after) // 2, wall, max(1, (after - before) // 2))

    # -- syncs ---------------------------------------------------------------------------
    def sync(
        self,
        domain: ClockDomain,
        clock_id: str = "",
        *,
        nominal_latency_ns: int | None = None,
        latency_uncertainty_ns: int | None = None,
    ) -> ClockSync:
        domain = ClockDomain(domain)
        if domain not in FOREIGN_DOMAINS:
            raise ValueError(f"{domain.value} is a native clock and needs no sync")
        key = (domain, clock_id)
        with self._lock:
            existing = self._syncs.get(key)
            if existing is None:
                existing = ClockSync(
                    domain,
                    clock_id,
                    config=self._sync_config,
                    nominal_latency_ns=nominal_latency_ns or 0,
                    latency_uncertainty_ns=latency_uncertainty_ns or 0,
                )
                self._syncs[key] = existing
            elif nominal_latency_ns is not None:
                existing.nominal_latency_ns = int(nominal_latency_ns)
                if latency_uncertainty_ns is not None:
                    existing.latency_uncertainty_ns = int(latency_uncertainty_ns)
            return existing

    def observe(self, source: TimeStamp, host_receive: TimeStamp | None = None) -> SyncOutcome:
        """Feed one (source time, host receive time) pair to the source's sync."""
        return self.sync(source.domain, source.clock_id).observe(source, host_receive or self.now())

    def syncs(self) -> Iterable[ClockSync]:
        return list(self._syncs.values())

    # -- conversion -------------------------------------------------------------------
    def to_host(self, ts: TimeStamp) -> TimeStamp:
        if ts.domain is ClockDomain.HOST_MONOTONIC:
            return ts
        if ts.domain is ClockDomain.UTC_WALL:
            mono_anchor, wall_anchor, gap = self._anchor
            return TimeStamp(
                mono_anchor + (ts.value_ns - wall_anchor),
                ClockDomain.HOST_MONOTONIC,
                _rss(gap, ts.uncertainty_ns),
            )
        key = ts.clock_key
        sync = self._syncs.get(key)
        if sync is None:
            raise ClockConversionError(f"no clock sync registered for {ts.describe()}")
        return sync.to_host(ts)

    def convert(self, ts: TimeStamp, domain: ClockDomain, clock_id: str = "") -> TimeStamp:
        domain = ClockDomain(domain)
        if ts.domain is domain and ts.clock_id == clock_id:
            return ts
        host = self.to_host(ts)
        if domain is ClockDomain.HOST_MONOTONIC:
            return host
        if domain is ClockDomain.UTC_WALL:
            mono_anchor, wall_anchor, gap = self._anchor
            return TimeStamp(
                wall_anchor + (host.value_ns - mono_anchor),
                ClockDomain.UTC_WALL,
                _rss(gap, host.uncertainty_ns),
            )
        return self.sync(domain, clock_id).from_host(host)

    def age(self, ts: TimeStamp, now: TimeStamp | None = None) -> Elapsed:
        """``now - ts`` on host monotonic time, with the conversion uncertainty."""
        now = now or self.now()
        if now.domain is not ClockDomain.HOST_MONOTONIC:
            now = self.to_host(now)
        return now - self.to_host(ts)

    def is_stale(self, ts: TimeStamp | None, max_age_s: float, now: TimeStamp | None = None) -> bool:
        """True when ``ts`` is older than ``max_age_s`` — or its age is unknown."""
        if ts is None:
            return True
        try:
            return self.age(ts, now).seconds > max_age_s
        except ClockConversionError:
            return True

    def stats(self) -> dict:
        return {
            "canonical": ClockDomain.HOST_MONOTONIC.value,
            "fake": self._fake is not None,
            "monotonic_s": round(self.monotonic_s(), 6),
            "utc_anchor_uncertainty_us": round(self._anchor[2] / 1e3, 3),
            "syncs": [s.stats() for s in self._syncs.values()],
        }


# -- process default -----------------------------------------------------------------------
_default_clock: VantaClock | None = None
_default_lock = threading.Lock()


def default_clock() -> VantaClock:
    """The process-wide clock. VantaSystem (PR B) will inject it explicitly."""
    global _default_clock
    with _default_lock:
        if _default_clock is None:
            _default_clock = VantaClock()
        return _default_clock


def set_default_clock(clock: VantaClock | None) -> None:
    global _default_clock
    with _default_lock:
        _default_clock = clock


# -- deterministic identifiers -------------------------------------------------------------
class SequentialIds:
    """UUIDs from a counter: identical across runs with the same seed."""

    def __init__(self, seed: int = 0) -> None:
        self._seed = int(seed) & 0xFFFFFFFF
        self._counter = itertools.count(1)

    def __call__(self) -> uuid.UUID:
        return uuid.UUID(int=(self._seed << 96) | next(self._counter))


__all__ = [
    "ClockConversionError",
    "ClockDomain",
    "ClockSync",
    "ClockSyncConfig",
    "Elapsed",
    "FakeTimeSource",
    "MixedClockDomainError",
    "NS_PER_S",
    "SequentialIds",
    "SyncOutcome",
    "TimeStamp",
    "VantaClock",
    "default_clock",
    "set_default_clock",
]
