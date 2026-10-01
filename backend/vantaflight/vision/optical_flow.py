"""Pyramidal Lucas-Kanade sparse optical flow utility."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class OpticalFlowResult:
    previous_points: np.ndarray
    current_points: np.ndarray
    valid: np.ndarray
    errors: np.ndarray

    @property
    def displacement(self) -> np.ndarray:
        return self.current_points - self.previous_points


class PyramidalLK:
    def __init__(
        self,
        window_size: tuple[int, int] = (21, 21),
        max_level: int = 3,
        max_error: float = 30.0,
        forward_backward_threshold: float = 1.5,
    ) -> None:
        self.window_size = window_size
        self.max_level = max_level
        self.max_error = max_error
        self.forward_backward_threshold = forward_backward_threshold

    @staticmethod
    def _gray(image: np.ndarray) -> np.ndarray:
        return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def track(self, previous: np.ndarray, current: np.ndarray, points: np.ndarray) -> OpticalFlowResult:
        original = np.asarray(points, np.float32).reshape(-1, 2)
        if not len(original):
            return OpticalFlowResult(original, original.copy(), np.zeros(0, bool), np.zeros(0))
        lk = dict(
            winSize=self.window_size,
            maxLevel=self.max_level,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
        )
        next_points, status, errors = cv2.calcOpticalFlowPyrLK(
            self._gray(previous), self._gray(current), original.reshape(-1, 1, 2), None, **lk
        )
        if next_points is None:
            return OpticalFlowResult(original, original.copy(), np.zeros(len(original), bool),
                                     np.full(len(original), np.inf))
        back_points, back_status, _ = cv2.calcOpticalFlowPyrLK(
            self._gray(current), self._gray(previous), next_points, None, **lk
        )
        tracked = next_points.reshape(-1, 2)
        backward = back_points.reshape(-1, 2) if back_points is not None else np.full_like(original, np.inf)
        error = errors.reshape(-1) if errors is not None else np.full(len(original), np.inf)
        fb_error = np.linalg.norm(backward - original, axis=1)
        valid = (
            status.reshape(-1).astype(bool)
            & (back_status.reshape(-1).astype(bool) if back_status is not None else False)
            & (error <= self.max_error)
            & (fb_error <= self.forward_backward_threshold)
        )
        return OpticalFlowResult(original, tracked, valid, error)
