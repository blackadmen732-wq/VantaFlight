"""Configurable OpenCV preprocessing with stage-level timing."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np


@dataclass(frozen=True)
class PreprocessConfig:
    resize: tuple[int, int] | None = None
    blur_kernel: int = 0
    clahe_clip_limit: float = 0.0
    clahe_grid: tuple[int, int] = (8, 8)
    convert_hsv: bool = False
    undistort: bool = False

    def __post_init__(self) -> None:
        if self.blur_kernel < 0 or (self.blur_kernel and self.blur_kernel % 2 == 0):
            raise ValueError("blur_kernel must be zero or a positive odd number")


@dataclass(frozen=True)
class PreprocessResult:
    image: np.ndarray
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def total_ms(self) -> float:
        return sum(self.timings_ms.values())


class OpenCVPreprocessor:
    def __init__(
        self,
        config: PreprocessConfig | None = None,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.config = config or PreprocessConfig()
        self._clock = clock

    def process(
        self,
        image: np.ndarray,
        camera_matrix: np.ndarray | None = None,
        distortion: np.ndarray | None = None,
    ) -> PreprocessResult:
        output = np.asarray(image)
        timings: dict[str, float] = {}

        def stage(name: str, operation: Callable[[np.ndarray], np.ndarray]) -> None:
            nonlocal output
            start = self._clock()
            output = operation(output)
            timings[name] = max(0.0, (self._clock() - start) * 1000)

        if self.config.undistort:
            if camera_matrix is None or distortion is None:
                raise ValueError("camera calibration required for undistortion")
            stage("undistort", lambda frame: cv2.undistort(frame, camera_matrix, distortion))
        if self.config.resize is not None:
            stage("resize", lambda frame: cv2.resize(frame, self.config.resize))
        if self.config.clahe_clip_limit > 0:
            def clahe(frame: np.ndarray) -> np.ndarray:
                lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                l_chan, a_chan, b_chan = cv2.split(lab)
                enhancer = cv2.createCLAHE(self.config.clahe_clip_limit, self.config.clahe_grid)
                return cv2.cvtColor(cv2.merge((enhancer.apply(l_chan), a_chan, b_chan)), cv2.COLOR_LAB2BGR)
            stage("clahe", clahe)
        if self.config.blur_kernel:
            stage(
                "blur",
                lambda frame: cv2.GaussianBlur(
                    frame, (self.config.blur_kernel, self.config.blur_kernel), 0
                ),
            )
        if self.config.convert_hsv:
            stage("hsv", lambda frame: cv2.cvtColor(frame, cv2.COLOR_BGR2HSV))
        return PreprocessResult(output, timings)
