"""Bounded, reproducible parameter experiments for course generation."""
from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
from typing import Callable, Mapping, Sequence

import numpy as np

from .generator import CourseGenerator, GenerationConfig
from .models import Course, CourseMode


@dataclass(frozen=True)
class ParameterBounds:
    minimum: float
    maximum: float
    integer: bool = False

    def __post_init__(self) -> None:
        if not math.isfinite(self.minimum) or not math.isfinite(self.maximum):
            raise ValueError("parameter bounds must be finite")
        if self.minimum > self.maximum:
            raise ValueError("parameter minimum must not exceed maximum")
        if self.integer and math.ceil(self.minimum) > math.floor(self.maximum):
            raise ValueError("integer parameter bounds contain no integers")

    def constrain(self, value: float | int) -> float | int:
        number = float(value)
        if not self.minimum <= number <= self.maximum:
            raise ValueError(f"value {number} is outside [{self.minimum}, {self.maximum}]")
        if not self.integer:
            return number
        constrained = int(round(number))
        if constrained < math.ceil(self.minimum) or constrained > math.floor(self.maximum):
            raise ValueError(f"value {number} does not round to an integer within bounds")
        return constrained


@dataclass(frozen=True)
class ExperimentRecord:
    candidate: Mapping[str, float | int]
    generation_seed: int
    score: float | None
    result: Mapping[str, float]
    error: str | None = None


Objective = Callable[[Course], float | Mapping[str, float]]


class ParameterExperiment:
    """Evaluate candidate configs while preserving the generator defaults."""

    def __init__(
        self,
        generator: CourseGenerator,
        bounds: Mapping[str, ParameterBounds | Sequence[float]],
        objective: Objective | None = None,
    ) -> None:
        self.generator = generator
        parsed: dict[str, ParameterBounds] = {}
        fields = GenerationConfig.__dataclass_fields__
        for name, value in bounds.items():
            if name not in fields:
                raise ValueError(f"unknown generation parameter {name!r}")
            if isinstance(value, ParameterBounds):
                parsed[name] = value
            else:
                if len(value) not in (2, 3):
                    raise ValueError("bounds are (minimum, maximum[, integer])")
                parsed[name] = ParameterBounds(
                    float(value[0]), float(value[1]), bool(value[2]) if len(value) == 3 else False
                )
        if not parsed:
            raise ValueError("at least one bounded parameter is required")
        self.bounds = dict(parsed)
        self.objective = objective
        self.records: list[ExperimentRecord] = []

    def _evaluate(
        self,
        candidate: Mapping[str, float | int],
        mode: CourseMode,
        generation_seed: int,
    ) -> ExperimentRecord:
        recorded_candidate: Mapping[str, float | int] = dict(candidate)
        try:
            constrained = {
                name: self.bounds[name].constrain(value) for name, value in candidate.items()
            }
            recorded_candidate = dict(constrained)
            # with_parameters creates a new frozen config; generator.config and
            # module defaults remain unchanged across all evaluations.
            config = self.generator.config.with_parameters(constrained)
            course = self.generator.generate(mode, seed=generation_seed, config=config)
            raw = self.objective(course) if self.objective else course.difficulty
            if isinstance(raw, Mapping):
                result = {str(key): float(value) for key, value in raw.items()}
                score = result.get("score")
            else:
                score = float(raw)
                result = {"score": score}
            if any(not math.isfinite(value) for value in result.values()):
                raise ValueError("objective results must be finite")
            record = ExperimentRecord(dict(constrained), generation_seed, score, result)
        except (ValueError, RuntimeError) as exc:
            record = ExperimentRecord(recorded_candidate, generation_seed, None, {}, str(exc))
        self.records.append(record)
        return record

    def grid_search(
        self,
        grid: Mapping[str, Sequence[float | int]],
        *,
        mode: CourseMode | str = CourseMode.RANDOM,
        generation_seed: int = 0,
    ) -> tuple[ExperimentRecord, ...]:
        if set(grid) != set(self.bounds):
            raise ValueError("grid parameters must exactly match experiment bounds")
        names = tuple(sorted(grid))
        for name in names:
            for value in grid[name]:
                self.bounds[name].constrain(value)
        start = len(self.records)
        for values in itertools.product(*(grid[name] for name in names)):
            self._evaluate(dict(zip(names, values)), CourseMode(mode), generation_seed)
        return tuple(self.records[start:])

    def random_search(
        self,
        trials: int,
        *,
        seed: int = 0,
        mode: CourseMode | str = CourseMode.RANDOM,
        generation_seed: int | None = None,
    ) -> tuple[ExperimentRecord, ...]:
        if trials < 0:
            raise ValueError("trials must be nonnegative")
        rng = np.random.default_rng(int(seed))
        start = len(self.records)
        names = tuple(sorted(self.bounds))
        for trial in range(trials):
            candidate: dict[str, float | int] = {}
            for name in names:
                bound = self.bounds[name]
                if bound.integer:
                    candidate[name] = int(rng.integers(
                        math.ceil(bound.minimum), math.floor(bound.maximum) + 1
                    ))
                else:
                    candidate[name] = float(rng.uniform(bound.minimum, bound.maximum))
            course_seed = int(generation_seed if generation_seed is not None else rng.integers(0, 2**31))
            self._evaluate(candidate, CourseMode(mode), course_seed + (0 if generation_seed is None else trial))
        return tuple(self.records[start:])


ExperimentRunner = ParameterExperiment
