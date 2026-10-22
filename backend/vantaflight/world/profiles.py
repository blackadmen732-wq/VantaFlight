from __future__ import annotations

from dataclasses import dataclass, replace
import math


@dataclass(frozen=True)
class WindField:
    steady_mps: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gust_mps: float = 0.0
    turbulence: float = 0.0

    def __post_init__(self) -> None:
        if not all(math.isfinite(float(v)) for v in (*self.steady_mps, self.gust_mps, self.turbulence)):
            raise ValueError("wind values must be finite")
        if self.gust_mps < 0 or self.turbulence < 0:
            raise ValueError("wind variability cannot be negative")


@dataclass(frozen=True)
class EnvironmentProfile:
    name: str
    wind: WindField = WindField()
    temperature_c: float = 22.0
    pressure_hpa: float = 1013.25
    humidity: float = 0.45
    visibility: float = 1.0
    light_level: float = 1.0
    glare: float = 0.0
    sensor_noise_scale: float = 1.0
    command_latency_ms: float = 0.0
    camera_latency_ms: float = 0.0
    payload_mass_g: float = 0.0
    battery_scale: float = 1.0

    def __post_init__(self) -> None:
        values = (
            self.temperature_c,
            self.pressure_hpa,
            self.humidity,
            self.visibility,
            self.light_level,
            self.glare,
            self.sensor_noise_scale,
            self.command_latency_ms,
            self.camera_latency_ms,
            self.payload_mass_g,
            self.battery_scale,
        )
        if not all(math.isfinite(float(v)) for v in values):
            raise ValueError("environment values must be finite")
        if self.pressure_hpa <= 0 or self.sensor_noise_scale < 0 or self.command_latency_ms < 0 or self.camera_latency_ms < 0:
            raise ValueError("invalid environment physical values")
        if self.payload_mass_g < 0 or self.battery_scale <= 0:
            raise ValueError("invalid payload/battery scale")
        for value, name in ((self.humidity, "humidity"), (self.visibility, "visibility"), (self.light_level, "light_level"), (self.glare, "glare")):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


def built_in_environment_profiles() -> dict[str, EnvironmentProfile]:
    calm = EnvironmentProfile(name="CALM_INDOOR")
    return {
        calm.name: calm,
        "HOT_FLORIDA_AFTERNOON": replace(
            calm,
            name="HOT_FLORIDA_AFTERNOON",
            temperature_c=34.0,
            humidity=0.70,
            light_level=0.95,
            glare=0.25,
        ),
        "GUSTY_OUTDOOR": replace(
            calm,
            name="GUSTY_OUTDOOR",
            wind=WindField((2.0, 0.6, 0.0), gust_mps=3.0, turbulence=0.7),
        ),
        "LOW_LIGHT": replace(
            calm,
            name="LOW_LIGHT",
            light_level=0.22,
            visibility=0.75,
            sensor_noise_scale=1.8,
        ),
        "SIDE_LIGHT_GLARE": replace(
            calm,
            name="SIDE_LIGHT_GLARE",
            glare=0.80,
            visibility=0.82,
        ),
        "PAYLOAD_HEAVY": replace(
            calm,
            name="PAYLOAD_HEAVY",
            payload_mass_g=25.0,
            battery_scale=0.86,
        ),
        "LOW_BATTERY": replace(
            calm,
            name="LOW_BATTERY",
            battery_scale=0.35,
        ),
    }
