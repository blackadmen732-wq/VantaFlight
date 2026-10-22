from __future__ import annotations

from .models import PayloadProfile, PayloadState


class PayloadManager:
    """Tracks attachment geometry and verified pickup/release state.

    Passive attachments have no actuator command; verification comes from the
    mission/perception layer. Future active attachments can implement a
    separate hardware adapter without changing mission geometry semantics.
    """

    def __init__(self, profile: PayloadProfile | None = None) -> None:
        self._state = PayloadState(profile=profile)

    @property
    def state(self) -> PayloadState:
        return self._state

    @property
    def profile(self) -> PayloadProfile | None:
        return self._state.profile

    def configure(self, profile: PayloadProfile | None) -> PayloadState:
        if self._state.loaded:
            raise RuntimeError("cannot change payload profile while an object is attached")
        self._state = PayloadState(profile=profile)
        return self._state

    def confirm_pickup(self, object_id: str) -> PayloadState:
        if self._state.profile is None:
            raise RuntimeError("no payload attachment is configured")
        if not object_id:
            raise ValueError("object_id is required")
        self._state = PayloadState(
            profile=self._state.profile,
            attached_object_id=object_id,
            verified=True,
        )
        return self._state

    def confirm_release(self) -> PayloadState:
        if not self._state.loaded:
            raise RuntimeError("no object is currently attached")
        self._state = PayloadState(profile=self._state.profile, verified=True)
        return self._state

    def reset_unverified(self) -> PayloadState:
        self._state = PayloadState(profile=self._state.profile)
        return self._state
