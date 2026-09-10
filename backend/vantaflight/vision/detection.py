"""Classical color/contour/polygon target detector."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .concepts import TargetCandidate, TargetProfile


@dataclass(frozen=True)
class DetectorConfig:
    morphology_kernel: int = 3
    approximation_epsilon: float = 0.025
    max_candidates: int = 16


def order_corners_clockwise(points: np.ndarray) -> np.ndarray:
    """Return quadrilateral corners in TL, TR, BR, BL order."""
    points = np.asarray(points, dtype=np.float64).reshape(4, 2)
    center = points.mean(axis=0)
    angles = np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0])
    ordered = points[np.argsort(angles)]
    # Cyclic order starts at top-left; reverse if it is counter to image order.
    start = int(np.argmin(ordered.sum(axis=1)))
    ordered = np.roll(ordered, -start, axis=0)
    if ordered[1, 0] < ordered[-1, 0]:
        ordered = ordered[[0, 3, 2, 1]]
    return ordered


class ClassicalTargetDetector:
    def __init__(self, profiles: list[TargetProfile], config: DetectorConfig | None = None) -> None:
        self.profiles = tuple(profiles)
        self.config = config or DetectorConfig()

    def detect(
        self,
        image: np.ndarray,
        timestamp: float,
        roi: tuple[int, int, int, int] | None = None,
        frame_id: str | None = None,
    ) -> list[TargetCandidate]:
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("detector expects a BGR image")
        height, width = image.shape[:2]
        x0, y0, rw, rh = roi or (0, 0, width, height)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x0 + max(0, rw)), min(height, y0 + max(0, rh))
        if x1 <= x0 or y1 <= y0:
            return []
        hsv = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        candidates: list[TargetCandidate] = []
        kernel_size = self.config.morphology_kernel
        kernel = np.ones((kernel_size, kernel_size), np.uint8) if kernel_size > 1 else None

        for profile in self.profiles:
            if not profile.color_ranges:
                continue
            mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
            for lower, upper in profile.color_ranges:
                mask = cv2.bitwise_or(
                    mask, cv2.inRange(hsv, np.array(lower), np.array(upper))
                )
            if kernel is not None:
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = float(cv2.contourArea(contour))
                if not profile.min_area_px <= area <= profile.max_area_px:
                    continue
                hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
                solidity = area / hull_area if hull_area > 0 else 0.0
                if solidity < profile.min_solidity:
                    continue
                perimeter = cv2.arcLength(contour, True)
                polygon = cv2.approxPolyDP(contour, self.config.approximation_epsilon * perimeter, True)
                if profile.polygon_vertices == 4 and len(polygon) == 4:
                    corners = polygon.reshape(4, 2).astype(np.float64)
                elif profile.polygon_vertices == 4:
                    corners = cv2.boxPoints(cv2.minAreaRect(contour))
                else:
                    if len(polygon) != profile.polygon_vertices:
                        continue
                    corners = polygon.reshape(-1, 2).astype(np.float64)
                if len(corners) != len(profile.object_points):
                    continue
                corners[:, 0] += x0
                corners[:, 1] += y0
                if len(corners) == 4:
                    corners = order_corners_clockwise(corners)
                rect = cv2.minAreaRect(contour)
                rw_box, rh_box = rect[1]
                ratio = max(rw_box, rh_box) / max(1e-9, min(rw_box, rh_box))
                lo, hi = profile.aspect_ratio_range
                if not lo <= ratio <= hi:
                    continue
                moments = cv2.moments(contour)
                cx = x0 + moments["m10"] / moments["m00"]
                cy = y0 + moments["m01"] / moments["m00"]
                fill = min(1.0, area / max(1.0, rw_box * rh_box))
                score = float(np.clip(0.55 * solidity + 0.45 * fill, 0, 1))
                shifted_contour = contour.copy()
                shifted_contour[:, 0, 0] += x0
                shifted_contour[:, 0, 1] += y0
                candidates.append(
                    TargetCandidate(
                        profile, corners, shifted_contour, (cx, cy), area, score, timestamp,
                        frame_id=frame_id,
                        color_confidence=fill,
                        edge_confidence=min(1.0, len(polygon) / max(1, profile.expected_corners)),
                        shape_confidence=solidity,
                        geometry_confidence=score,
                    )
                )
        candidates.sort(key=lambda candidate: (candidate.score, candidate.area_px), reverse=True)
        return candidates[: self.config.max_candidates]
