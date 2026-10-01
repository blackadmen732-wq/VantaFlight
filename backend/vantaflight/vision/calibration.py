"""Camera calibration utility — checkerboard detection and intrinsic estimation."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .concepts import CameraProfile


@dataclass
class CalibrationSample:
    image_path: str
    corners_found: bool
    corner_count: int
    reprojection_error: float = 0.0


@dataclass
class CalibrationResult:
    success: bool
    camera_matrix: np.ndarray | None = None
    distortion: np.ndarray | None = None
    reprojection_error: float = 0.0
    samples_used: int = 0
    total_samples: int = 0
    resolution: tuple[int, int] | None = None
    calibration_version: str = ""
    error_message: str = ""
    samples: list[CalibrationSample] = field(default_factory=list)

    def to_profile(self, camera_id: str = "calibrated", fps: float = 30.0) -> CameraProfile:
        if not self.success or self.camera_matrix is None:
            raise ValueError("calibration not successful")
        return CameraProfile(
            camera_matrix=self.camera_matrix,
            distortion=self.distortion,
            resolution=self.resolution,
            camera_id=camera_id,
            fps=fps,
            calibration_version=self.calibration_version,
        )

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "camera_matrix": self.camera_matrix.tolist() if self.camera_matrix is not None else None,
            "distortion": self.distortion.tolist() if self.distortion is not None else None,
            "reprojection_error": round(self.reprojection_error, 6),
            "samples_used": self.samples_used,
            "total_samples": self.total_samples,
            "resolution": list(self.resolution) if self.resolution else None,
            "calibration_version": self.calibration_version,
            "error_message": self.error_message,
        }

    def save(self, path: str | Path) -> None:
        data = self.to_dict()
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "CalibrationResult":
        data = json.loads(Path(path).read_text())
        return cls(
            success=data["success"],
            camera_matrix=np.array(data["camera_matrix"]) if data.get("camera_matrix") else None,
            distortion=np.array(data["distortion"]) if data.get("distortion") else None,
            reprojection_error=data.get("reprojection_error", 0.0),
            samples_used=data.get("samples_used", 0),
            total_samples=data.get("total_samples", 0),
            resolution=tuple(data["resolution"]) if data.get("resolution") else None,
            calibration_version=data.get("calibration_version", ""),
            error_message=data.get("error_message", ""),
        )


class CameraCalibrator:
    """Checkerboard-based camera calibration.

    Collects images of a checkerboard pattern, detects corners,
    and estimates camera intrinsics using OpenCV's calibrateCamera.
    """

    def __init__(
        self,
        board_size: tuple[int, int] = (9, 6),
        square_size_mm: float = 25.0,
        min_samples: int = 10,
    ) -> None:
        self._board_size = board_size
        self._square_size_mm = square_size_mm
        self._min_samples = max(3, min_samples)
        self._obj_points: list[np.ndarray] = []
        self._img_points: list[np.ndarray] = []
        self._image_size: tuple[int, int] | None = None
        self._samples: list[CalibrationSample] = []

        objp = np.zeros((board_size[0] * board_size[1], 3), np.float32)
        objp[:, :2] = np.mgrid[0:board_size[0], 0:board_size[1]].T.reshape(-1, 2)
        objp *= square_size_mm
        self._objp_template = objp

    @property
    def sample_count(self) -> int:
        return len(self._obj_points)

    @property
    def min_samples(self) -> int:
        return self._min_samples

    @property
    def ready(self) -> bool:
        return self.sample_count >= self._min_samples

    def add_image(self, image: np.ndarray, source: str = "") -> CalibrationSample:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        h, w = gray.shape[:2]

        if self._image_size is None:
            self._image_size = (w, h)
        elif self._image_size != (w, h):
            return CalibrationSample(
                image_path=source, corners_found=False, corner_count=0
            )

        found, corners = cv2.findChessboardCorners(
            gray, self._board_size,
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE,
        )

        sample = CalibrationSample(
            image_path=source,
            corners_found=bool(found),
            corner_count=int(corners.shape[0]) if found and corners is not None else 0,
        )

        if found and corners is not None:
            refined = cv2.cornerSubPix(
                gray, corners, (11, 11), (-1, -1),
                (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001),
            )
            self._obj_points.append(self._objp_template)
            self._img_points.append(refined)

        self._samples.append(sample)
        return sample

    def add_images_from_directory(self, directory: str | Path, pattern: str = "*.jpg") -> int:
        dir_path = Path(directory)
        added = 0
        for img_path in sorted(dir_path.glob(pattern)):
            image = cv2.imread(str(img_path))
            if image is not None:
                sample = self.add_image(image, str(img_path))
                if sample.corners_found:
                    added += 1
        return added

    def calibrate(self, camera_id: str = "calibrated") -> CalibrationResult:
        if not self.ready:
            return CalibrationResult(
                success=False,
                total_samples=len(self._samples),
                error_message=f"need at least {self._min_samples} samples, have {self.sample_count}",
                samples=list(self._samples),
            )

        assert self._image_size is not None

        rms, camera_matrix, distortion, rvecs, tvecs = cv2.calibrateCamera(
            self._obj_points, self._img_points, self._image_size, None, None
        )

        version = f"cal-{camera_id}-{int(time.time())}"

        return CalibrationResult(
            success=True,
            camera_matrix=camera_matrix,
            distortion=distortion.flatten(),
            reprojection_error=float(rms),
            samples_used=self.sample_count,
            total_samples=len(self._samples),
            resolution=self._image_size,
            calibration_version=version,
            samples=list(self._samples),
        )

    def reset(self) -> None:
        self._obj_points.clear()
        self._img_points.clear()
        self._image_size = None
        self._samples.clear()
