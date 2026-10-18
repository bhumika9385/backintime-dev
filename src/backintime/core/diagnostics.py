"""Read-only health diagnostics for snapshot repositories.

Every check here only inspects the filesystem. Diagnostics never create, repair,
move, or delete repository data; they report what was found and suggest next steps.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from backintime.core.exceptions import ValidationError
from backintime.core.snapshot_id import SnapshotID

DEFAULT_MIN_FREE_BYTES = 1024**3
_METADATA_FILE = "info.json"


class HealthState(str, Enum):
    """Health of a snapshot or repository, ordered from best to worst."""

    HEALTHY = "healthy"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"
    INACCESSIBLE = "inaccessible"

    @property
    def rank(self) -> int:
        return list(HealthState).index(self)


class Severity(str, Enum):
    """How urgently a finding needs attention."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Finding:
    """One diagnostic result with an actionable next step."""

    code: str
    severity: Severity
    subject: str
    message: str
    guidance: str


@dataclass(frozen=True, slots=True)
class SnapshotDiagnosis:
    """Health of one directory named like a snapshot."""

    snapshot_id: str
    path: Path
    state: HealthState
    finding: Finding | None = None


class DiskUsage(NamedTuple):
    total: int
    used: int
    free: int


@dataclass(frozen=True, slots=True)
class RepositoryReport:
    """Diagnostics for one snapshot repository."""

    repository: Path
    state: HealthState
    snapshots: tuple[SnapshotDiagnosis, ...] = ()
    findings: tuple[Finding, ...] = ()
    disk_usage: DiskUsage | None = None

    @property
    def healthy_snapshot_count(self) -> int:
        return sum(1 for snapshot in self.snapshots if snapshot.state is HealthState.HEALTHY)


def diagnose_repository(
    repository: Path,
    *,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    disk_usage: Callable[[Path], DiskUsage] | None = None,
) -> RepositoryReport:
    """Inspect a repository and its snapshots without modifying anything."""
    root = repository.expanduser()
    subject = str(root)

    if not root.exists():
        return RepositoryReport(
            repository=root,
            state=HealthState.INACCESSIBLE,
            findings=(
                Finding(
                    code="TARGET_MISSING",
                    severity=Severity.ERROR,
                    subject=subject,
                    message="The backup target does not exist.",
                    guidance=(
                        "If the target is on a removable or network drive, connect and mount "
                        "it, then run diagnostics again. Otherwise check the profile target "
                        "path."
                    ),
                ),
            ),
        )
    if root.is_symlink() or not root.is_dir():
        return RepositoryReport(
            repository=root,
            state=HealthState.INVALID,
            findings=(
                Finding(
                    code="TARGET_NOT_DIRECTORY",
                    severity=Severity.ERROR,
                    subject=subject,
                    message="The backup target is not a regular directory.",
                    guidance="Point the profile target at a real directory, not a file or link.",
                ),
            ),
        )

    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        return RepositoryReport(
            repository=root,
            state=HealthState.INACCESSIBLE,
            findings=(
                Finding(
                    code="TARGET_UNREADABLE",
                    severity=Severity.ERROR,
                    subject=subject,
                    message=f"The backup target cannot be listed: {exc.strerror or exc}.",
                    guidance="Check that the current user can read and enter the target directory.",
                ),
            ),
        )

    snapshots = tuple(
        sorted(
            (diagnosis for entry in entries if (diagnosis := _diagnose_entry(entry)) is not None),
            key=lambda diagnosis: diagnosis.snapshot_id,
            reverse=True,
        )
    )
    findings = [snapshot.finding for snapshot in snapshots if snapshot.finding is not None]
    state = max((snapshot.state for snapshot in snapshots), key=lambda s: s.rank, default=None)
    state = state or HealthState.HEALTHY

    if not any(snapshot.state is HealthState.HEALTHY for snapshot in snapshots):
        findings.append(
            Finding(
                code="NO_HEALTHY_SNAPSHOTS",
                severity=Severity.WARNING if snapshots else Severity.INFO,
                subject=subject,
                message="The repository contains no complete snapshots.",
                guidance=(
                    "No restorable backup exists in this repository yet. Keep the original "
                    "data until a backup completes successfully."
                ),
            )
        )

    usage: DiskUsage | None = None
    try:
        usage = (disk_usage or _disk_usage)(root)
    except OSError as exc:
        findings.append(
            Finding(
                code="DISK_USAGE_UNAVAILABLE",
                severity=Severity.WARNING,
                subject=subject,
                message=f"Free space could not be determined: {exc.strerror or exc}.",
                guidance="Check the target filesystem manually before the next backup.",
            )
        )
    else:
        if usage.free < min_free_bytes:
            findings.append(
                Finding(
                    code="LOW_DISK_SPACE",
                    severity=Severity.WARNING,
                    subject=subject,
                    message=(
                        f"Only {_format_bytes(usage.free)} free on the target filesystem "
                        f"(threshold {_format_bytes(min_free_bytes)})."
                    ),
                    guidance=(
                        "Free space on the target drive or move the repository before the "
                        "next backup. Do not delete snapshot directories by hand."
                    ),
                )
            )

    return RepositoryReport(
        repository=root,
        state=state,
        snapshots=snapshots,
        findings=tuple(findings),
        disk_usage=usage,
    )


def _diagnose_entry(entry: Path) -> SnapshotDiagnosis | None:
    """Diagnose an entry named like a snapshot; ignore unrelated names."""
    try:
        snapshot_id = SnapshotID.parse(entry.name)
    except ValidationError:
        return None
    name = str(snapshot_id)

    def result(state: HealthState, code: str, message: str, guidance: str) -> SnapshotDiagnosis:
        severity = Severity.WARNING if state is HealthState.INCOMPLETE else Severity.ERROR
        return SnapshotDiagnosis(
            snapshot_id=name,
            path=entry,
            state=state,
            finding=Finding(
                code=code, severity=severity, subject=name, message=message, guidance=guidance
            ),
        )

    keep = "Do not restore from it; keep it until a newer snapshot completes."
    if entry.is_symlink() or not entry.is_dir():
        return result(
            HealthState.INVALID,
            "SNAPSHOT_NOT_DIRECTORY",
            "Snapshot entry is a link or file instead of a directory.",
            "It is ignored by snapshot listings. Inspect it manually before removing it.",
        )
    if not os.access(entry, os.R_OK | os.X_OK):
        return result(
            HealthState.INACCESSIBLE,
            "SNAPSHOT_UNREADABLE",
            "Snapshot directory cannot be read by the current user.",
            "Check the directory owner and permissions.",
        )

    metadata_path = entry / _METADATA_FILE
    if metadata_path.is_symlink():
        return result(
            HealthState.INVALID,
            "METADATA_SYMLINK",
            "Snapshot metadata is a symbolic link.",
            f"Metadata links are not trusted. {keep}",
        )
    if not metadata_path.exists():
        return result(
            HealthState.INCOMPLETE,
            "METADATA_MISSING",
            "Snapshot has no metadata; the backup was probably interrupted.",
            keep,
        )
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return result(
            HealthState.INACCESSIBLE,
            "METADATA_UNREADABLE",
            f"Snapshot metadata cannot be read: {exc.strerror or exc}.",
            "Check the file permissions and the health of the target drive.",
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return result(
            HealthState.INVALID,
            "METADATA_MALFORMED",
            "Snapshot metadata is not valid JSON.",
            f"The metadata may be damaged. {keep}",
        )
    if not isinstance(metadata, dict):
        return result(
            HealthState.INVALID,
            "METADATA_MALFORMED",
            "Snapshot metadata is not a JSON object.",
            f"The metadata may be damaged. {keep}",
        )
    if metadata.get("snapshot_id") != name:
        return result(
            HealthState.INVALID,
            "METADATA_ID_MISMATCH",
            "Snapshot metadata names a different snapshot than its directory.",
            f"The snapshot may have been renamed or copied. {keep}",
        )
    snapshot_state = metadata.get("state")
    if snapshot_state != "complete":
        described = repr(snapshot_state) if isinstance(snapshot_state, str) else "missing"
        return result(
            HealthState.INCOMPLETE,
            "SNAPSHOT_NOT_COMPLETE",
            f"Snapshot state is {described}, not 'complete'.",
            keep,
        )
    return SnapshotDiagnosis(snapshot_id=name, path=entry, state=HealthState.HEALTHY)


def _disk_usage(path: Path) -> DiskUsage:
    usage = shutil.disk_usage(path)
    return DiskUsage(total=usage.total, used=usage.used, free=usage.free)


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"
