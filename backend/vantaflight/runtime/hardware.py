"""HardwareProfiler — measure actual device capabilities without uploading."""
from __future__ import annotations

import multiprocessing
import os
import platform
import sys
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class HardwareInfo:
    cpu_arch: str
    cpu_count: int
    ram_total_mb: int
    os_name: str
    os_version: str
    python_version: str
    opencv_version: str
    has_shared_memory: bool
    numpy_version: str

    def to_dict(self) -> dict:
        return {
            "cpu_arch": self.cpu_arch,
            "cpu_count": self.cpu_count,
            "ram_total_mb": self.ram_total_mb,
            "os_name": self.os_name,
            "os_version": self.os_version,
            "python_version": self.python_version,
            "opencv_version": self.opencv_version,
            "has_shared_memory": self.has_shared_memory,
            "numpy_version": self.numpy_version,
        }


class HardwareProfiler:

    @staticmethod
    def profile() -> HardwareInfo:
        ram_mb = 0
        try:
            if platform.system() == "Linux":
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            ram_mb = int(line.split()[1]) // 1024
                            break
            elif platform.system() == "Darwin":
                ram_mb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
        except Exception:
            pass

        has_shm = False
        try:
            from multiprocessing import shared_memory
            buf = shared_memory.SharedMemory(create=True, size=64)
            buf.close()
            buf.unlink()
            has_shm = True
        except Exception:
            pass

        return HardwareInfo(
            cpu_arch=platform.machine(),
            cpu_count=multiprocessing.cpu_count() or 1,
            ram_total_mb=ram_mb,
            os_name=platform.system(),
            os_version=platform.release(),
            python_version=sys.version.split()[0],
            opencv_version=cv2.__version__,
            has_shared_memory=has_shm,
            numpy_version=np.__version__,
        )

    @staticmethod
    def benchmark_vision(iterations: int = 50) -> dict:
        """Quick local benchmark: HSV segmentation + contour finding."""
        image = np.random.randint(0, 256, (480, 640, 3), dtype=np.uint8)
        import time
        start = time.monotonic()
        for _ in range(iterations):
            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, np.array([0, 100, 100]), np.array([15, 255, 255]))
            cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        elapsed = time.monotonic() - start
        return {
            "iterations": iterations,
            "total_s": round(elapsed, 3),
            "per_frame_ms": round(elapsed / iterations * 1000, 2),
            "estimated_fps": round(iterations / elapsed, 1) if elapsed > 0 else 0,
        }
