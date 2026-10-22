from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PayloadProfile:
    payload_id: str
    kind: str
    mass_g: float
    body_offset_m: tuple[float, float, float]
    contact_offset_m: tuple[float, float, float]

    def __post_init__(self) -> None:
        if not self.payload_id or not self.kind:
            raise ValueError("payload id/kind required")
        values = (self.mass_g, *self.body_offset_m, *self.contact_offset_m)
        if not all(math.isfinite(float(v)) for v in values):
            raise ValueError("payload geometry must be finite")
        if self.mass_g < 0:
            raise ValueError("payload mass cannot be negative")


@dataclass(frozen=True)
class PassiveHookProfile(PayloadProfile):
    hook_length_m: float = 0.10

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.hook_length_m <= 0 or not math.isfinite(self.hook_length_m):
            raise ValueError("hook length must be finite and positive")

    @classmethod
    def hopper_default(
        cls,
        *,
        contact_offset_m: tuple[float, float, float] = (0.0, 0.0, -0.10),
        mass_g: float = 3.0,
    ) -> "PassiveHookProfile":
        return cls(
            payload_id="hopper-passive-hook",
            kind="PASSIVE_HOOK",
            mass_g=mass_g,
            body_offset_m=(0.0, 0.0, -0.05),
            contact_offset_m=contact_offset_m,
            hook_length_m=abs(contact_offset_m[2]),
        )


@dataclass(frozen=True)
class PayloadState:
    profile: PayloadProfile | None
    attached_object_id: str | None = None
    verified: bool = False

    @property
    def loaded(self) -> bool:
        return self.attached_object_id is not None
