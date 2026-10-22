"""VantaAuthority — who may move the aircraft, proven by a lease on every attempt.

Authority is never inferred from which module published last. It follows a
fixed hierarchy::

    FAILSAFE > EMERGENCY_OPERATOR > OPERATOR > APPROVED_MISSION_AUTOMATION
             > AGENT_PROPOSAL > TRAINING_OBSERVATION

and is granted as an ``AuthorityLease``: owner, level, scope, vehicle, issue
and expiry times, and a revocation token only the authority knows. An
execution attempt must present its lease and the authority checks it is
still the live record — unexpired, unrevoked, covering the scope and the
vehicle — so a stale or forged lease is refused.

Rules:

* A higher level preempts (revokes) overlapping lower-level leases; a lower
  or equal level cannot take what another owner holds.
* Mission automation needs an approver (the operator who started it) and a
  finite lease that its holder renews; it cannot outrank an operator or a
  failsafe.
* Agents and training (below mission automation) can never hold an
  execution scope on a physical vehicle: they may propose, observe, or act
  in simulation only. Unknown vehicles count as physical.
* Failsafes and operators take over with ``preempt``; a failsafe can hold a
  lock-out lease until it is cleared, so automation cannot restart mid-recovery.

Every grant, denial, revocation, release and preemption is recorded and, with
a bus attached, published as an ``authority.decision`` reliable event.
"""
from __future__ import annotations

import enum
import logging
import secrets
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable

from .clock import ClockDomain, TimeStamp, VantaClock, default_clock

logger = logging.getLogger(__name__)


class AuthorityLevel(enum.IntEnum):
    TRAINING_OBSERVATION = 10
    AGENT_PROPOSAL = 20
    APPROVED_MISSION_AUTOMATION = 30
    OPERATOR = 40
    EMERGENCY_OPERATOR = 50
    FAILSAFE = 60


#: Scopes that move or configure the aircraft.
EXECUTION_SCOPES = frozenset(
    {"trajectory", "offboard", "arm", "takeoff", "land", "return", "goto", "hold", "mission", "payload"}
)
#: Scopes that never touch the aircraft.
ADVISORY_SCOPES = frozenset({"proposal", "observe", "simulation"})
ALL_SCOPES = "*"
KNOWN_SCOPES = EXECUTION_SCOPES | ADVISORY_SCOPES | {ALL_SCOPES}

#: Longest lease below operator level; holders renew it while they act.
MAX_AUTOMATION_TTL_S = 30.0


class AuthorityError(PermissionError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AuthorityDenied(AuthorityError):
    """A lease was not granted."""


class LeaseInvalid(AuthorityError):
    """A presented lease does not authorize the attempt."""


@dataclass(frozen=True)
class AuthorityLease:
    lease_id: str
    owner: str
    level: AuthorityLevel
    scope: frozenset[str]
    vehicle_id: str
    issued_at: TimeStamp
    expires_at: TimeStamp | None
    revocation_token: str = field(repr=False)
    approved_by: str | None = None

    def covers(self, scope: str) -> bool:
        return ALL_SCOPES in self.scope or scope in self.scope

    def overlaps(self, scope: Iterable[str]) -> bool:
        scope = set(scope)
        if ALL_SCOPES in self.scope or ALL_SCOPES in scope:
            return True
        return bool(self.scope & scope)

    def to_dict(self, now: TimeStamp | None = None) -> dict:
        out = {
            "lease_id": self.lease_id,
            "owner": self.owner,
            "level": self.level.name,
            "scope": sorted(self.scope),
            "vehicle_id": self.vehicle_id,
            "approved_by": self.approved_by,
            "issued_at": self.issued_at.to_dict(),
            "expires_at": self.expires_at.to_dict() if self.expires_at is not None else None,
        }
        if now is not None and self.expires_at is not None:
            out["expires_in_s"] = round((self.expires_at - now).seconds, 3)
        return out


@dataclass
class _Record:
    lease: AuthorityLease
    revoked: str | None = None  # reason


@dataclass(frozen=True)
class AuthorityDecision:
    action: str  # grant | deny | release | revoke | preempt | expire
    granted: bool
    owner: str
    level: AuthorityLevel
    scope: tuple[str, ...]
    vehicle_id: str
    reason: str
    lease_id: str | None
    approved_by: str | None
    at: TimeStamp
    expires_in_s: float | None = None

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "granted": self.granted,
            "owner": self.owner,
            "level": self.level.name,
            "scope": list(self.scope),
            "vehicle_id": self.vehicle_id,
            "reason": self.reason,
            "lease_id": self.lease_id,
            "approved_by": self.approved_by,
            "at": self.at.to_dict(),
            "expires_in_s": self.expires_in_s,
        }


class VantaAuthority:
    def __init__(self, clock: VantaClock | None = None, *, history: int = 500) -> None:
        self._clock = clock or default_clock()
        self._records: dict[str, _Record] = {}
        self._vehicles: dict[str, bool] = {}  # vehicle_id -> simulated
        self._decisions: deque[AuthorityDecision] = deque(maxlen=history)
        self._listeners: list[Callable[[AuthorityDecision], None]] = []
        self._lock = threading.RLock()

    # -- vehicles -------------------------------------------------------------------------------
    def register_vehicle(self, vehicle_id: str, *, simulated: bool) -> None:
        with self._lock:
            self._vehicles[vehicle_id] = bool(simulated)

    def unregister_vehicle(self, vehicle_id: str, reason: str = "vehicle disconnected") -> None:
        with self._lock:
            self._vehicles.pop(vehicle_id, None)
            for record in self._records.values():
                if record.lease.vehicle_id == vehicle_id and record.revoked is None:
                    self._revoke_locked(record, reason, action="revoke")

    def is_simulated(self, vehicle_id: str) -> bool:
        return self._vehicles.get(vehicle_id, False)

    def on_decision(self, listener: Callable[[AuthorityDecision], None]) -> None:
        self._listeners.append(listener)

    # -- time ------------------------------------------------------------------------------------
    def now(self) -> TimeStamp:
        return self._clock.now(ClockDomain.HOST_MONOTONIC)

    def _expired(self, lease: AuthorityLease, now: TimeStamp) -> bool:
        return lease.expires_at is not None and now >= lease.expires_at

    # -- granting ----------------------------------------------------------------------------------
    def acquire(
        self,
        owner: str,
        level: AuthorityLevel,
        scope: Iterable[str],
        vehicle_id: str,
        *,
        ttl_s: float | None,
        approved_by: str | None = None,
    ) -> AuthorityLease:
        """Grant a lease or raise ``AuthorityDenied`` (the denial is recorded too)."""
        level = AuthorityLevel(level)
        scope_set = frozenset(scope)
        with self._lock:
            now = self.now()
            problem = self._grant_problem(owner, level, scope_set, vehicle_id, ttl_s, approved_by, now)
            if problem is not None:
                self._decide("deny", False, owner, level, scope_set, vehicle_id, problem, None, approved_by, now)
                raise AuthorityDenied(problem)
            # Preempt overlapping lower-level leases; replace the owner's own.
            for record in list(self._active_locked(vehicle_id, now)):
                lease = record.lease
                if not lease.overlaps(scope_set):
                    continue
                if lease.owner == owner:
                    self._revoke_locked(record, "replaced by a new lease from the same owner", action="release")
                elif lease.level < level:
                    self._revoke_locked(record, f"preempted by {owner} ({level.name})", action="revoke")
            lease = AuthorityLease(
                lease_id=str(uuid.uuid4()),
                owner=owner,
                level=level,
                scope=scope_set,
                vehicle_id=vehicle_id,
                issued_at=now,
                expires_at=None if ttl_s is None else now.plus_s(ttl_s),
                revocation_token=secrets.token_hex(16),
                approved_by=approved_by,
            )
            self._records[lease.lease_id] = _Record(lease)
            self._decide(
                "grant", True, owner, level, scope_set, vehicle_id,
                f"granted {', '.join(sorted(scope_set))}", lease.lease_id, approved_by, now, ttl_s,
            )
            return lease

    def _grant_problem(
        self,
        owner: str,
        level: AuthorityLevel,
        scope: frozenset[str],
        vehicle_id: str,
        ttl_s: float | None,
        approved_by: str | None,
        now: TimeStamp,
    ) -> str | None:
        if not owner:
            return "a lease needs an owner"
        if not scope:
            return "a lease needs a scope"
        unknown = scope - KNOWN_SCOPES
        if unknown:
            return f"unknown scope(s): {', '.join(sorted(unknown))}"
        if ttl_s is not None and not ttl_s > 0:
            return "ttl_s must be positive"
        execution = ALL_SCOPES in scope or bool(scope & EXECUTION_SCOPES)
        if level < AuthorityLevel.APPROVED_MISSION_AUTOMATION and execution and not self.is_simulated(vehicle_id):
            return (
                f"{level.name} cannot hold execution authority on physical vehicle {vehicle_id}; "
                "it may propose, observe or act in simulation"
            )
        if level <= AuthorityLevel.APPROVED_MISSION_AUTOMATION:
            if ttl_s is None:
                return f"{level.name} leases must expire (renew them while acting)"
            if ttl_s > MAX_AUTOMATION_TTL_S:
                return f"{level.name} leases last at most {MAX_AUTOMATION_TTL_S:g}s"
        if level is AuthorityLevel.APPROVED_MISSION_AUTOMATION and execution and not approved_by:
            return "mission automation needs the operator who approved it"
        for record in self._active_locked(vehicle_id, now):
            lease = record.lease
            if lease.owner != owner and lease.overlaps(scope) and lease.level >= level:
                return f"held by {lease.owner} ({lease.level.name})"
        return None

    def renew(self, lease: AuthorityLease, ttl_s: float) -> AuthorityLease:
        """Extend a still-valid lease (same id and token). Not recorded as a decision."""
        with self._lock:
            now = self.now()
            record = self._valid_record_locked(lease, None, None, now)
            if lease.level <= AuthorityLevel.APPROVED_MISSION_AUTOMATION and not (0 < ttl_s <= MAX_AUTOMATION_TTL_S):
                raise LeaseInvalid(f"renewal must be within 0..{MAX_AUTOMATION_TTL_S:g}s")
            renewed = replace(record.lease, expires_at=now.plus_s(ttl_s))
            record.lease = renewed
            return renewed

    def release(self, lease: AuthorityLease, reason: str = "released by owner") -> bool:
        with self._lock:
            record = self._records.get(lease.lease_id)
            if record is None or record.lease.revocation_token != lease.revocation_token:
                return False
            if record.revoked is None:
                self._revoke_locked(record, reason, action="release")
            # Keep the record (pruned later) so a stale holder learns *why* it was refused.
            self._prune_locked()
            return True

    def release_owner(self, owner: str, reason: str = "released by owner") -> int:
        with self._lock:
            count = 0
            for record in list(self._records.values()):
                if record.lease.owner == owner and record.revoked is None:
                    self._revoke_locked(record, reason, action="release")
                    count += 1
            self._prune_locked()
            return count

    def preempt(
        self,
        level: AuthorityLevel,
        owner: str,
        vehicle_id: str,
        *,
        reason: str,
        scope: Iterable[str] = (ALL_SCOPES,),
        hold: bool = False,
    ) -> list[AuthorityLease]:
        """Revoke every overlapping lease below ``level`` (failsafe / operator takeover).

        With ``hold`` the preempting owner keeps an open lease (a lock-out)
        until it releases it; only operator levels and above may do that.
        """
        level = AuthorityLevel(level)
        scope_set = frozenset(scope)
        if level < AuthorityLevel.OPERATOR:
            raise AuthorityDenied(f"{level.name} cannot preempt other leases")
        with self._lock:
            now = self.now()
            revoked: list[AuthorityLease] = []
            for record in list(self._active_locked(vehicle_id, now)):
                lease = record.lease
                if lease.level < level and lease.overlaps(scope_set) and lease.owner != owner:
                    self._revoke_locked(record, f"preempted by {owner}: {reason}", action="revoke")
                    revoked.append(lease)
            self._decide(
                "preempt", True, owner, level, scope_set, vehicle_id,
                f"{reason} (revoked {len(revoked)} lease(s))", None, None, now,
            )
            if hold:
                held = AuthorityLease(
                    lease_id=str(uuid.uuid4()), owner=owner, level=level, scope=scope_set,
                    vehicle_id=vehicle_id, issued_at=now, expires_at=None,
                    revocation_token=secrets.token_hex(16),
                )
                for record in list(self._active_locked(vehicle_id, now)):
                    if record.lease.owner == owner:
                        self._revoke_locked(record, "replaced by a new hold", action="release")
                self._records[held.lease_id] = _Record(held)
            return revoked

    # -- checking ------------------------------------------------------------------------------------
    def validate(self, lease: AuthorityLease | None, scope: str, *, vehicle_id: str | None = None) -> AuthorityLease:
        """Raise ``LeaseInvalid`` unless ``lease`` authorizes ``scope`` right now."""
        with self._lock:
            return self._valid_record_locked(lease, scope, vehicle_id, self.now()).lease

    def check(self, lease: AuthorityLease | None, scope: str, *, vehicle_id: str | None = None) -> str | None:
        """Like ``validate`` but returns the reason (None = authorized)."""
        try:
            self.validate(lease, scope, vehicle_id=vehicle_id)
        except LeaseInvalid as exc:
            return exc.reason
        return None

    def _valid_record_locked(
        self, lease: AuthorityLease | None, scope: str | None, vehicle_id: str | None, now: TimeStamp
    ) -> _Record:
        if lease is None:
            raise LeaseInvalid("no authority lease presented")
        record = self._records.get(lease.lease_id)
        if record is None or record.lease.revocation_token != lease.revocation_token:
            raise LeaseInvalid("unknown or forged lease")
        if record.revoked is not None:
            raise LeaseInvalid(f"lease revoked: {record.revoked}")
        if self._expired(record.lease, now):
            self._revoke_locked(record, "expired", action="expire")
            raise LeaseInvalid("lease expired")
        if scope is not None and not record.lease.covers(scope):
            raise LeaseInvalid(f"lease does not cover '{scope}'")
        if vehicle_id is not None and record.lease.vehicle_id != vehicle_id:
            raise LeaseInvalid(f"lease is for {record.lease.vehicle_id}, not {vehicle_id}")
        return record

    def _active_locked(self, vehicle_id: str, now: TimeStamp) -> list[_Record]:
        out = []
        for record in list(self._records.values()):
            if record.revoked is not None or record.lease.vehicle_id != vehicle_id:
                continue
            if self._expired(record.lease, now):
                self._revoke_locked(record, "expired", action="expire")
                continue
            out.append(record)
        return out

    def active_leases(self, vehicle_id: str | None = None) -> list[AuthorityLease]:
        with self._lock:
            now = self.now()
            vehicles = [vehicle_id] if vehicle_id is not None else sorted({r.lease.vehicle_id for r in self._records.values()})
            leases = [r.lease for v in vehicles for r in self._active_locked(v, now)]
            self._prune_locked()
            return sorted(leases, key=lambda lease: (-lease.level, lease.owner))

    def holder(self, vehicle_id: str, scope: str) -> AuthorityLease | None:
        """The highest-level live lease covering ``scope`` on the vehicle."""
        for lease in self.active_leases(vehicle_id):
            if lease.covers(scope):
                return lease
        return None

    # -- bookkeeping ------------------------------------------------------------------------------------
    def _revoke_locked(self, record: _Record, reason: str, *, action: str) -> None:
        if record.revoked is not None:
            return
        record.revoked = reason
        lease = record.lease
        self._decide(action, False, lease.owner, lease.level, lease.scope, lease.vehicle_id,
                     reason, lease.lease_id, lease.approved_by, self.now())

    def _prune_locked(self) -> None:
        # Keep revoked records briefly so holders learn *why*; cap the table.
        revoked = [k for k, r in self._records.items() if r.revoked is not None]
        for lease_id in revoked[:-256]:
            del self._records[lease_id]

    def _decide(
        self,
        action: str,
        granted: bool,
        owner: str,
        level: AuthorityLevel,
        scope: Iterable[str],
        vehicle_id: str,
        reason: str,
        lease_id: str | None,
        approved_by: str | None,
        at: TimeStamp,
        ttl_s: float | None = None,
    ) -> None:
        decision = AuthorityDecision(
            action, granted, owner, AuthorityLevel(level), tuple(sorted(scope)), vehicle_id,
            reason, lease_id, approved_by, at, ttl_s,
        )
        self._decisions.append(decision)
        level_name = AuthorityLevel(level).name
        log = logger.warning if action in ("deny", "revoke", "preempt") else logger.info
        log("authority %s: %s (%s) on %s: %s", action, owner, level_name, vehicle_id, reason)
        for listener in list(self._listeners):
            try:
                listener(decision)
            except Exception:
                logger.exception("authority listener failed")

    def decisions(self) -> list[AuthorityDecision]:
        return list(self._decisions)

    def status(self) -> dict:
        now = self.now()
        return {
            "hierarchy": [lvl.name for lvl in sorted(AuthorityLevel, reverse=True)],
            "vehicles": {v: ("simulated" if sim else "physical") for v, sim in sorted(self._vehicles.items())},
            "leases": [lease.to_dict(now) for lease in self.active_leases()],
            "recent_decisions": [d.to_dict() for d in list(self._decisions)[-50:]],
        }
