"""Reproducible fault injection for simulation stress-testing."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .models import FaultConfig, FaultType


@dataclass
class ActiveFault:
    fault_type: FaultType
    start_time: float
    duration_s: float
    magnitude: float

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.start_time > self.duration_s


class FaultInjector:
    """Applies reproducible faults to camera frames and telemetry streams.

    Each fault type has a probability of triggering per ``tick()`` call.
    Once triggered, the fault stays active for its configured duration.
    The RNG is seeded per-fault for reproducibility across runs.
    """

    def __init__(self, configs: list[FaultConfig] | None = None) -> None:
        self._configs: dict[FaultType, FaultConfig] = {}
        self._rngs: dict[FaultType, np.random.Generator] = {}
        self._active: dict[FaultType, ActiveFault] = {}
        self._history: list[dict] = []
        if configs:
            for cfg in configs:
                self.add_fault(cfg)

    def add_fault(self, config: FaultConfig) -> None:
        self._configs[config.fault_type] = config
        self._rngs[config.fault_type] = np.random.default_rng(config.seed)

    def remove_fault(self, fault_type: FaultType) -> None:
        self._configs.pop(fault_type, None)
        self._rngs.pop(fault_type, None)
        self._active.pop(fault_type, None)

    def tick(self) -> list[ActiveFault]:
        now = time.monotonic()
        expired = [ft for ft, af in self._active.items() if af.expired]
        for ft in expired:
            self._history.append({
                "fault_type": ft.value,
                "start": self._active[ft].start_time,
                "duration": self._active[ft].duration_s,
            })
            del self._active[ft]

        newly_active: list[ActiveFault] = []
        for ft, cfg in self._configs.items():
            if ft in self._active:
                continue
            if cfg.probability <= 0:
                continue
            if self._rngs[ft].random() < cfg.probability:
                af = ActiveFault(
                    fault_type=ft,
                    start_time=now,
                    duration_s=cfg.duration_s,
                    magnitude=cfg.magnitude,
                )
                self._active[ft] = af
                newly_active.append(af)

        return newly_active

    @property
    def active_faults(self) -> dict[FaultType, ActiveFault]:
        return dict(self._active)

    def is_active(self, fault_type: FaultType) -> bool:
        af = self._active.get(fault_type)
        return af is not None and not af.expired

    def apply_to_frame(self, frame: np.ndarray) -> np.ndarray:
        result = frame
        if self.is_active(FaultType.BLUR):
            mag = self._active[FaultType.BLUR].magnitude
            ksize = max(3, int(mag) | 1)
            result = _apply_blur(result, ksize)
        if self.is_active(FaultType.NOISE):
            mag = self._active[FaultType.NOISE].magnitude
            rng = self._rngs[FaultType.NOISE]
            result = _apply_noise(result, mag, rng)
        if self.is_active(FaultType.OCCLUSION):
            mag = self._active[FaultType.OCCLUSION].magnitude
            rng = self._rngs[FaultType.OCCLUSION]
            result = _apply_occlusion(result, mag, rng)
        if self.is_active(FaultType.LIGHTING_CHANGE):
            mag = self._active[FaultType.LIGHTING_CHANGE].magnitude
            result = _apply_lighting(result, mag)
        return result

    def should_drop_frame(self) -> bool:
        return self.is_active(FaultType.FRAME_DROP)

    def camera_delay_s(self) -> float:
        if self.is_active(FaultType.CAMERA_DELAY):
            return self._active[FaultType.CAMERA_DELAY].magnitude / 1000.0
        return 0.0

    def should_pause_telemetry(self) -> bool:
        return self.is_active(FaultType.TELEMETRY_PAUSE)

    def should_disconnect_px4(self) -> bool:
        return self.is_active(FaultType.PX4_DISCONNECT)

    def should_disconnect_camera(self) -> bool:
        return self.is_active(FaultType.CAMERA_DISCONNECT)

    def packet_delay_s(self) -> float:
        if self.is_active(FaultType.PACKET_DELAY):
            return self._active[FaultType.PACKET_DELAY].magnitude / 1000.0
        return 0.0

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    def reset(self) -> None:
        self._active.clear()
        self._history.clear()
        for ft, cfg in self._configs.items():
            self._rngs[ft] = np.random.default_rng(cfg.seed)

    def to_dict(self) -> dict:
        return {
            "configs": {ft.value: cfg.to_dict() for ft, cfg in self._configs.items()},
            "active": {ft.value: {"magnitude": af.magnitude, "remaining_s": max(0.0, af.duration_s - (time.monotonic() - af.start_time))} for ft, af in self._active.items()},
            "history_count": len(self._history),
        }


def _apply_blur(frame: np.ndarray, ksize: int) -> np.ndarray:
    import cv2
    return cv2.GaussianBlur(frame, (ksize, ksize), 0)


def _apply_noise(frame: np.ndarray, magnitude: float, rng: np.random.Generator) -> np.ndarray:
    noise = rng.normal(0, magnitude, frame.shape).astype(np.float32)
    return np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def _apply_occlusion(frame: np.ndarray, magnitude: float, rng: np.random.Generator) -> np.ndarray:
    h, w = frame.shape[:2]
    block_h = int(h * min(magnitude, 0.5))
    block_w = int(w * min(magnitude, 0.5))
    if block_h < 1 or block_w < 1:
        return frame
    y = int(rng.integers(0, max(1, h - block_h)))
    x = int(rng.integers(0, max(1, w - block_w)))
    result = frame.copy()
    result[y:y + block_h, x:x + block_w] = 0
    return result


def _apply_lighting(frame: np.ndarray, magnitude: float) -> np.ndarray:
    scale = max(0.2, min(2.0, magnitude))
    return np.clip(frame.astype(np.float32) * scale, 0, 255).astype(np.uint8)
