"""Configurable OpenCV preprocessing with stage-level timing."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

import cv2
import numpy as np


class PixelColorSpace(str, Enum):
    """Color interpretation for three-channel OpenCV images."""

    BGR = "BGR"
    HSV = "HSV"


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
    color_space: PixelColorSpace = PixelColorSpace.BGR
    camera_matrix: np.ndarray | None = None
    distortion: np.ndarray | None = None

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
        *,
        color_space: PixelColorSpace = PixelColorSpace.BGR,
    ) -> PreprocessResult:
        output = np.asarray(image)
        timings: dict[str, float] = {}
        effective_matrix = (
            None if camera_matrix is None else np.asarray(camera_matrix, np.float64).copy()
        )
        effective_distortion = (
            None if distortion is None else np.asarray(distortion, np.float64).copy()
        )
        if (effective_matrix is None) != (effective_distortion is None):
            raise ValueError("camera matrix and distortion must be supplied together")
        if color_space != PixelColorSpace.BGR:
            raise ValueError("preprocessor input must use BGR color space")
        input_height, input_width = output.shape[:2]

        def stage(name: str, operation: Callable[[np.ndarray], np.ndarray]) -> None:
            nonlocal output
            start = self._clock()
            output = operation(output)
            timings[name] = max(0.0, (self._clock() - start) * 1000)

        if self.config.undistort:
            if effective_matrix is None or effective_distortion is None:
                raise ValueError("camera calibration required for undistortion")
            stage(
                "undistort",
                lambda frame: cv2.undistort(frame, effective_matrix, effective_distortion),
            )
            effective_distortion = np.zeros_like(effective_distortion)
        if self.config.resize is not None:
            stage("resize", lambda frame: cv2.resize(frame, self.config.resize))
            if effective_matrix is not None:
                resized_width, resized_height = self.config.resize
                scale = np.diag(
                    [resized_width / input_width, resized_height / input_height, 1.0]
                )
                effective_matrix = scale @ effective_matrix
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
            color_space = PixelColorSpace.HSV
        return PreprocessResult(
            output,
            timings,
            color_space,
            effective_matrix,
            effective_distortion,
        )
