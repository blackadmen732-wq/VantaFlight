"""Region-of-interest search state and adaptive search policy."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ROIState(str, Enum):
    FULL_FRAME = "full_frame"
    TRACKING = "tracking"
    EXPANDING = "expanding"


@dataclass(frozen=True)
class ROIPolicyConfig:
    tracked_size_px: int = 160
    expansion_factor: float = 1.7
    full_frame_after_misses: int = 3


class ROISearchPolicy:
    def __init__(self, config: ROIPolicyConfig | None = None) -> None:
        self.config = config or ROIPolicyConfig()
        self.state = ROIState.FULL_FRAME
        self._center: tuple[float, float] | None = None
        self._misses = 0

    def found(self, center: tuple[float, float]) -> None:
        self._center = center
        self._misses = 0
        self.state = ROIState.TRACKING

    def missed(self) -> None:
        self._misses += 1
        if self._misses >= self.config.full_frame_after_misses:
            self.state = ROIState.FULL_FRAME
            self._center = None
        elif self._center is not None:
            self.state = ROIState.EXPANDING

    def region(self, image_shape: tuple[int, ...]) -> tuple[int, int, int, int]:
        height, width = image_shape[:2]
        if self.state == ROIState.FULL_FRAME or self._center is None:
            return (0, 0, width, height)
        size = self.config.tracked_size_px
        if self.state == ROIState.EXPANDING:
            size = int(size * self.config.expansion_factor ** self._misses)
        cx, cy = self._center
        x0, y0 = max(0, int(cx - size / 2)), max(0, int(cy - size / 2))
        x1, y1 = min(width, int(cx + size / 2)), min(height, int(cy + size / 2))
        return x0, y0, x1 - x0, y1 - y0
