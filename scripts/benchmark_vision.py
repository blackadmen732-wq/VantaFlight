#!/usr/bin/env python3
"""Deterministic CPU benchmark for the classical VantaSight pipeline."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from vantaflight.vision import (  # noqa: E402
    CameraProfile,
    ClassicalTargetDetector,
    FrameBuffer,
    FramePacket,
    KalmanTargetTracker,
    OpenCVPreprocessor,
    PoseEstimator,
    TargetProfile,
    VantaFusion,
    VisionEvidence,
    project_planar_target,
    render_target_image,
)


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else 0.0


def timed(call):
    started = time.perf_counter_ns()
    result = call()
    return result, (time.perf_counter_ns() - started) / 1_000_000


def run_benchmark(frames: int, burst: int, seed: int) -> dict[str, float | int]:
    if frames < 1 or burst < 1:
        raise ValueError("frames and burst must be positive")

    matrix = np.array([[520.0, 0.0, 320.0], [0.0, 520.0, 240.0], [0.0, 0.0, 1.0]])
    camera = CameraProfile(
        camera_matrix=matrix,
        distortion=np.zeros(5),
        resolution=(640, 480),
        camera_id="benchmark-camera",
        fps=60.0,
        calibration_version="synthetic-v1",
    )
    profile = TargetProfile.rectangle(
        "benchmark-gate",
        width_m=2.0,
        height_m=1.5,
        hsv_lower=(45, 120, 80),
        hsv_upper=(85, 255, 255),
        min_area_px=200,
    )
    preprocessor = OpenCVPreprocessor()
    detector = ClassicalTargetDetector([profile])
    estimator = PoseEstimator(camera)
    tracker = KalmanTargetTracker()
    fusion = VantaFusion()
    buffer = FrameBuffer(capacity=2)

    total_ms: list[float] = []
    preprocess_ms: list[float] = []
    detection_ms: list[float] = []
    pose_ms: list[float] = []
    tracking_ms: list[float] = []
    fusion_ms: list[float] = []
    pose_errors: list[float] = []
    tracked = 0
    started = time.perf_counter()
    sequence = 0

    for process_index in range(frames):
        for item in range(burst):
            phase = (process_index * burst + item) / max(1, frames * burst - 1)
            truth = np.array([0.45 * np.sin(phase * 4 * np.pi), 0.1, 5.0])
            rvec = np.array([0.03, -0.08, 0.02])
            corners = project_planar_target(
                profile.object_points, camera.camera_matrix, rvec, truth, camera.distortion
            )
            image = render_target_image(
                corners=corners,
                noise_std=1.5,
                seed=seed + sequence,
            )
            captured = time.monotonic()
            buffer.put(
                FramePacket(
                    image,
                    capture_timestamp=captured,
                    receive_timestamp=time.monotonic(),
                    sequence=sequence,
                    camera_source="synthetic-benchmark",
                    camera_profile=camera,
                    metadata={"truth_translation": truth.tolist()},
                )
            )
            sequence += 1

        packet = buffer.latest()
        if packet is None:
            continue
        frame_started = time.perf_counter_ns()
        processed, duration = timed(
            lambda: preprocessor.process(
                packet.image, camera.camera_matrix, camera.distortion
            )
        )
        preprocess_ms.append(duration)
        candidates, duration = timed(
            lambda: detector.detect(processed.image, packet.capture_timestamp)
        )
        detection_ms.append(duration)

        candidate = candidates[0] if candidates else None
        pose_result, duration = timed(
            lambda: estimator.estimate(candidate) if candidate is not None else None
        )
        pose_ms.append(duration)
        snapshot, duration = timed(
            lambda: tracker.update(
                None if candidate is None else np.asarray(candidate.center),
                packet.capture_timestamp,
            )
        )
        tracking_ms.append(duration)
        tracked += int(snapshot.initialized)

        evidence = []
        if candidate is not None:
            evidence.append(
                VisionEvidence(
                    "classical",
                    candidate.detector_confidence,
                    packet.capture_timestamp,
                    0.9,
                    "image",
                )
            )
        validated = None if pose_result is None else pose_result.validated
        if validated is not None:
            evidence.append(
                VisionEvidence(
                    "pose",
                    float(np.exp(-validated.reprojection_error_px / 3.0)),
                    packet.capture_timestamp,
                    0.8,
                    "geometry",
                )
            )
            truth = np.asarray(packet.metadata["truth_translation"])
            pose_errors.append(float(np.linalg.norm(validated.translation_vector - truth)))
        _, duration = timed(lambda: fusion.fuse(evidence, time.monotonic()))
        fusion_ms.append(duration)
        total_ms.append((time.perf_counter_ns() - frame_started) / 1_000_000)

    elapsed = time.perf_counter() - started
    metrics = buffer.metrics
    dropped = getattr(
        metrics,
        "dropped_frames",
        getattr(metrics, "dropped_capacity", 0) + getattr(metrics, "dropped_stale", 0),
    )
    processed_count = len(total_ms)
    return {
        "processed_frames": processed_count,
        "effective_fps": processed_count / elapsed if elapsed else 0.0,
        "dropped_frames": int(dropped),
        "median_latency_ms": percentile(total_ms, 50),
        "p95_latency_ms": percentile(total_ms, 95),
        "p99_latency_ms": percentile(total_ms, 99),
        "preprocess_median_ms": percentile(preprocess_ms, 50),
        "detection_median_ms": percentile(detection_ms, 50),
        "pose_median_ms": percentile(pose_ms, 50),
        "tracking_median_ms": percentile(tracking_ms, 50),
        "fusion_median_ms": percentile(fusion_ms, 50),
        "pose_error_median_m": percentile(pose_errors, 50),
        "tracking_continuity": tracked / processed_count if processed_count else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=250)
    parser.add_argument("--burst", type=int, default=2)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run_benchmark(args.frames, args.burst, args.seed)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for name, value in result.items():
            print(f"{name}: {value:.3f}" if isinstance(value, float) else f"{name}: {value}")


if __name__ == "__main__":
    main()
