from __future__ import annotations

import importlib.util
from pathlib import Path


def test_vision_benchmark_reports_measured_stage_metrics():
    path = Path(__file__).parents[2] / "scripts" / "benchmark_vision.py"
    spec = importlib.util.spec_from_file_location("benchmark_vision", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.run_benchmark(frames=4, burst=2, seed=3)
    assert result["processed_frames"] == 4
    assert result["dropped_frames"] == 4
    assert result["effective_fps"] > 0
    assert result["median_latency_ms"] > 0
    assert result["p95_latency_ms"] >= result["median_latency_ms"]
    assert result["pose_error_median_m"] >= 0
    assert 0 <= result["tracking_continuity"] <= 1
