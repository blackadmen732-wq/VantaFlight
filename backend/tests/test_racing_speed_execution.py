from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vantaflight.racing import (
    AutonomousExecutionRejected,
    SimulationOnlyExecutionGuard,
    SpeedEnvelopeConfig,
    speed_envelope,
)


def constraints(size: int = 6) -> dict[str, np.ndarray]:
    return {
        "curvature": np.zeros(size),
        "vertical_gradient": np.zeros(size),
        "clearance": np.full(size, 10.0),
        "confidence": np.ones(size),
        "uncertainty": np.zeros(size),
    }


def test_speed_profile_obeys_forward_acceleration_and_backward_braking() -> None:
    distance = np.arange(6, dtype=float)
    values = constraints()
    values["curvature"][-1] = 50
    config = SpeedEnvelopeConfig(
        max_speed=20,
        max_forward_acceleration=2,
        max_braking_acceleration=3,
        aggression=1,
    )
    speed = speed_envelope(distance, initial_speed=0, config=config, **values)
    assert np.all(speed[1:] ** 2 - speed[:-1] ** 2 <= 2 * 2 * np.diff(distance) + 1e-10)
    assert np.all(speed[:-1] ** 2 - speed[1:] ** 2 <= 2 * 3 * np.diff(distance) + 1e-10)
    assert speed[-2] < speed[2]


def test_each_local_constraint_reduces_speed() -> None:
    distance = np.arange(6, dtype=float)
    baseline = speed_envelope(distance, initial_speed=50, config=SpeedEnvelopeConfig(aggression=1), **constraints())
    constrained = constraints()
    constrained["curvature"][3] = 4.0
    constrained["vertical_gradient"][3] = 3.0
    constrained["clearance"][3] = 0.5
    constrained["confidence"][3] = 0.1
    constrained["uncertainty"][3] = 3.0
    result = speed_envelope(
        distance,
        initial_speed=50,
        config=SpeedEnvelopeConfig(aggression=1),
        **constrained,
    )
    assert result[3] < baseline[3]


def test_aggression_scaling_is_continuous_and_monotonic() -> None:
    distance = np.linspace(0, 100, 6)
    values = constraints()
    speeds = [
        speed_envelope(
            distance,
            initial_speed=100,
            config=SpeedEnvelopeConfig(aggression=aggression),
            **values,
        )[-1]
        for aggression in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    assert np.all(np.diff(speeds) > 0)
    assert speeds[2] == pytest.approx((speeds[0] + speeds[-1]) / 2)


def test_explicit_final_speed_propagates_braking_backward() -> None:
    distance = np.arange(6, dtype=float)
    speed = speed_envelope(
        distance,
        initial_speed=20,
        final_speed=1,
        config=SpeedEnvelopeConfig(max_braking_acceleration=2, aggression=1),
        **constraints(),
    )
    assert speed[-1] == 1
    assert speed[-2] <= np.sqrt(1 + 4)


def simulation_capabilities(**overrides):
    values = {
        "is_simulated": True,
        "adapter_type": "px4_sitl",
        "supported_capabilities": ["position", "simulation"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_execution_guard_authorizes_only_explicit_local_simulator() -> None:
    permit = SimulationOnlyExecutionGuard.authorize(
        "udp://127.0.0.1:14540", simulation_capabilities()
    )
    assert permit.adapter_type == "px4_sitl"
    assert permit.endpoint.endswith(":14540")


@pytest.mark.parametrize(
    ("endpoint", "capabilities"),
    [
        ("udp://127.0.0.1:14540", simulation_capabilities(is_simulated=False)),
        ("udp://127.0.0.1:14540", simulation_capabilities(adapter_type="physical")),
        ("udp://127.0.0.1:14540", simulation_capabilities(supported_capabilities=["position"])),
        ("udp://192.168.1.22:14540", simulation_capabilities()),
        ("udp://127.0.0.1:14539", simulation_capabilities()),
        ("udp://127.0.0.1:14581", simulation_capabilities()),
        ("tcp://127.0.0.1:14540", simulation_capabilities()),
        ("serial://localhost/dev/ttyUSB0", simulation_capabilities()),
        ("", simulation_capabilities()),
    ],
)
def test_execution_guard_rejects_physical_or_non_sitl_targets(endpoint, capabilities) -> None:
    with pytest.raises(AutonomousExecutionRejected):
        SimulationOnlyExecutionGuard.authorize(endpoint, capabilities)


def test_racing_package_has_no_opencv_imports() -> None:
    package = Path(__file__).parents[1] / "vantaflight" / "racing"
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text())
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        assert "cv2" not in imports
        assert "opencv" not in imports
