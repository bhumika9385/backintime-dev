"""Retention policies and a read-only preview of which snapshots they would remove.

Nothing in this module deletes data. :func:`plan_retention` only decides which
snapshots a policy would keep or remove so users can inspect the result first.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from enum import Enum

from backintime.core.exceptions import ValidationError
from backintime.core.repository import SnapshotRecord

_MAX_KEEP = 100_000


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """How many snapshots to keep overall and per calendar period.

    Each ``keep_*`` period rule keeps the newest snapshot in each of that many
    most recent periods (days, ISO weeks, months, years) that contain one.
    """

    keep_last: int = 0
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0
    keep_yearly: int = 0

    def __post_init__(self) -> None:
        for rule in fields(self):
            value = getattr(self, rule.name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(f"Retention rule '{rule.name}' must be an integer.")
            if not 0 <= value <= _MAX_KEEP:
                raise ValidationError(
                    f"Retention rule '{rule.name}' must be between 0 and {_MAX_KEEP}."
                )
        if not any(getattr(self, rule.name) for rule in fields(self)):
            raise ValidationError("A retention policy must keep at least one snapshot.")

    @classmethod
    def from_mapping(cls, value: object) -> RetentionPolicy:
        """Build a policy from a profile's ``retention`` JSON object."""
        if not isinstance(value, Mapping):
            raise ValidationError("Retention settings must be a JSON object.")
        known = {rule.name for rule in fields(cls)}
        unknown = sorted(str(key) for key in value if key not in known)
        if unknown:
            raise ValidationError(f"Unknown retention rule(s): {', '.join(unknown)}.")
        return cls(**{str(key): item for key, item in value.items()})


class Action(str, Enum):
    KEEP = "keep"
    REMOVE = "remove"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class PlanEntry:
    """The decision for one snapshot and why it was made."""

    snapshot_id: str
    action: Action
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """A dry-run result, ordered newest snapshot first."""

    policy: RetentionPolicy
    entries: tuple[PlanEntry, ...]

    def _ids(self, action: Action) -> tuple[str, ...]:
        return tuple(entry.snapshot_id for entry in self.entries if entry.action is action)

    @property
    def keep(self) -> tuple[str, ...]:
        return self._ids(Action.KEEP)

    @property
    def remove(self) -> tuple[str, ...]:
        return self._ids(Action.REMOVE)

    @property
    def skip(self) -> tuple[str, ...]:
        return self._ids(Action.SKIP)


_PERIODS: tuple[tuple[str, str, Callable[[datetime], object]], ...] = (
    ("keep_daily", "daily", lambda moment: moment.date()),
    ("keep_weekly", "weekly", lambda moment: moment.isocalendar()[:2]),
    ("keep_monthly", "monthly", lambda moment: (moment.year, moment.month)),
    ("keep_yearly", "yearly", lambda moment: moment.year),
)


def plan_retention(records: Iterable[SnapshotRecord], policy: RetentionPolicy) -> RetentionPlan:
    """Decide which snapshots ``policy`` keeps, without touching the filesystem.

    Safety rules that override the policy:

    - Only snapshots with validated, complete metadata can be selected for
      removal; incomplete or invalid ones are skipped and left for diagnostics.
    - Protected snapshots and snapshots with an explicit name are always kept.
    - The newest valid snapshot is always kept, so pruning can never remove
      the last restorable backup.
    """
    ordered = sorted(records, key=lambda record: record.snapshot_id, reverse=True)
    reasons: dict[str, list[str]] = {str(record.snapshot_id): [] for record in ordered}
    valid = [record for record in ordered if record.healthy]

    if valid:
        reasons[str(valid[0].snapshot_id)].append("newest valid snapshot")
    for record in valid:
        if record.protected:
            reasons[str(record.snapshot_id)].append("protected")
        if record.name:
            reasons[str(record.snapshot_id)].append(f"named '{record.name}'")
    for record in valid[: policy.keep_last]:
        reasons[str(record.snapshot_id)].append("keep_last")
    for rule, label, period_of in _PERIODS:
        remaining = getattr(policy, rule)
        last_period: object = None
        for record in valid:
            if remaining == 0:
                break
            period = period_of(record.snapshot_id.timestamp)
            if period != last_period:
                reasons[str(record.snapshot_id)].append(label)
                last_period = period
                remaining -= 1

    entries: list[PlanEntry] = []
    for record in ordered:
        snapshot_id = str(record.snapshot_id)
        if not record.healthy:
            entries.append(
                PlanEntry(
                    snapshot_id,
                    Action.SKIP,
                    ("metadata is incomplete or invalid; never selected for removal",),
                )
            )
        elif reasons[snapshot_id]:
            entries.append(PlanEntry(snapshot_id, Action.KEEP, tuple(reasons[snapshot_id])))
        else:
            entries.append(PlanEntry(snapshot_id, Action.REMOVE, ("not matched by any rule",)))
    return RetentionPlan(policy=policy, entries=tuple(entries))
