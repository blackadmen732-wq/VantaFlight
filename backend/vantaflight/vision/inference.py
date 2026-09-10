"""Optional asynchronous ONNX-style detector boundary and freshness handling."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol, runtime_checkable

import numpy as np

from .concepts import FramePacket, TargetCandidate


@dataclass(frozen=True)
class AsyncDetectionResult:
    candidates: tuple[TargetCandidate, ...]
    frame_timestamp: float
    completed_timestamp: float
    sequence: int
    backend: str = "onnx"

    def is_fresh(self, now: float, max_age_s: float) -> bool:
        return 0 <= now - self.frame_timestamp <= max_age_s


@runtime_checkable
class AsyncDetector(Protocol):
    async def detect(self, frame: FramePacket) -> AsyncDetectionResult: ...


class ONNXDetector:
    """Adapter for an injected ONNX callable; onnxruntime remains optional.

    The callable receives an image and returns target candidates. Supplying the
    session-specific decoding callable keeps this core NumPy/OpenCV-only.
    """

    def __init__(
        self,
        infer: Callable[[np.ndarray, float], list[TargetCandidate]]
        | Callable[[np.ndarray, float], Awaitable[list[TargetCandidate]]],
        *,
        backend: str = "onnx",
        clock: Callable[[], float] = time.monotonic,
        run_in_thread: bool = True,
    ) -> None:
        self._infer = infer
        self._backend = backend
        self._clock = clock
        self._run_in_thread = run_in_thread

    async def detect(self, frame: FramePacket) -> AsyncDetectionResult:
        if self._run_in_thread:
            candidates = await asyncio.to_thread(self._infer, frame.image, frame.timestamp)
        else:
            candidates = self._infer(frame.image, frame.timestamp)
            if asyncio.iscoroutine(candidates):
                candidates = await candidates
        return AsyncDetectionResult(
            tuple(candidates), frame.timestamp, self._clock(), frame.sequence, self._backend
        )
