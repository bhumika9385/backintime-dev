"""Cancellable local snapshot creation."""

import fnmatch
import json
import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from backintime.core.exceptions import BackupCancelledError, ValidationError
from backintime.core.profile import Profile
from backintime.core.repository import Repository
from backintime.core.snapshot_id import SnapshotID

_COPY_CHUNK_SIZE = 1024 * 1024
ProgressCallback = Callable[[int, int], None]
SnapshotCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Outcome details for one completed snapshot."""

    snapshot_id: str
    files_copied: int
    bytes_copied: int


def create_snapshot(
    profile: Profile,
    cancellation: Event,
    report_progress: ProgressCallback,
    report_snapshot: SnapshotCallback,
) -> BackupResult:
    """Copy configured sources into an incomplete snapshot and commit it last."""
    profile.validate()
    target = profile.target.expanduser().resolve(strict=False)
    target.mkdir(parents=True, exist_ok=True)
    snapshot_id, snapshot_root = _create_snapshot_directory(target)
    snapshot_created = False
    counters = [0, 0]

    try:
        _write_metadata(snapshot_root, profile, snapshot_id, "running")
        snapshot_created = True
        report_snapshot(str(snapshot_id))
        data_root = snapshot_root / "data"
        data_root.mkdir()

        for index, configured_source in enumerate(profile.sources, start=1):
            _check_cancelled(cancellation)
            source = configured_source.expanduser().resolve(strict=True)
            if not source.is_file() and not source.is_dir():
                raise ValidationError(
                    "A configured backup source is not a regular file or directory."
                )

            source_target = data_root / f"source-{index:03d}"
            if source.is_dir():
                source_target.mkdir()
                _copy_directory(
                    source,
                    source_target,
                    profile.excludes,
                    cancellation,
                    counters,
                    report_progress,
                )
            else:
                relative_name = Path(source.name)
                if not _is_excluded(relative_name, profile.excludes):
                    source_target.mkdir()
                    _copy_file(
                        source,
                        source_target / source.name,
                        cancellation,
                        counters,
                        report_progress,
                    )

        _check_cancelled(cancellation)
        _write_metadata(snapshot_root, profile, snapshot_id, "complete")
        return BackupResult(str(snapshot_id), counters[0], counters[1])
    except BackupCancelledError:
        if snapshot_created:
            _write_metadata(snapshot_root, profile, snapshot_id, "cancelled")
        raise
    except Exception:
        if snapshot_created:
            _write_metadata(snapshot_root, profile, snapshot_id, "failed")
        raise


def _create_snapshot_directory(target: Path) -> tuple[SnapshotID, Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for sequence in range(1000):
        snapshot_id = SnapshotID(f"{timestamp}-{sequence:03d}")
        snapshot_root = Repository(target).snapshot_path(snapshot_id)
        try:
            snapshot_root.mkdir()
        except FileExistsError:
            continue
        return snapshot_id, snapshot_root
    raise FileExistsError("Cannot allocate a unique snapshot ID for this second.")


def _write_metadata(
    snapshot_root: Path,
    profile: Profile,
    snapshot_id: SnapshotID,
    state: str,
) -> None:
    metadata: dict[str, object] = {
        "snapshot_id": str(snapshot_id),
        "profile_id": profile.profile_id,
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary_path = snapshot_root / ".info.json.tmp"
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(metadata, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, snapshot_root / "info.json")
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _copy_directory(
    source: Path,
    destination: Path,
    excludes: tuple[str, ...],
    cancellation: Event,
    counters: list[int],
    report_progress: ProgressCallback,
) -> None:
    pending: list[tuple[Path, Path, Path]] = [(source, destination, Path())]
    directories: list[tuple[Path, Path]] = []

    while pending:
        _check_cancelled(cancellation)
        current_source, current_target, relative_directory = pending.pop()
        directories.append((current_source, current_target))
        with os.scandir(current_source) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name, reverse=True)

        for entry in entries:
            _check_cancelled(cancellation)
            relative_path = relative_directory / entry.name
            if _is_excluded(relative_path, excludes):
                continue

            source_entry = Path(entry.path)
            target_entry = current_target / entry.name
            if entry.is_symlink():
                link_target = os.readlink(source_entry)
                os.symlink(
                    link_target,
                    target_entry,
                    target_is_directory=entry.is_dir(follow_symlinks=True),
                )
                counters[0] += 1
                counters[1] += len(os.fsencode(link_target))
                report_progress(counters[0], counters[1])
            elif entry.is_dir(follow_symlinks=False):
                target_entry.mkdir()
                pending.append((source_entry, target_entry, relative_path))
            elif entry.is_file(follow_symlinks=False):
                _copy_file(
                    source_entry,
                    target_entry,
                    cancellation,
                    counters,
                    report_progress,
                )
            else:
                raise ValidationError("A backup source contains an unsupported filesystem entry.")

    for source_directory, target_directory in reversed(directories):
        _check_cancelled(cancellation)
        shutil.copystat(source_directory, target_directory, follow_symlinks=False)


def _copy_file(
    source: Path,
    destination: Path,
    cancellation: Event,
    counters: list[int],
    report_progress: ProgressCallback,
) -> None:
    with source.open("rb") as source_stream, destination.open("xb") as target_stream:
        while chunk := source_stream.read(_COPY_CHUNK_SIZE):
            _check_cancelled(cancellation)
            target_stream.write(chunk)
            counters[1] += len(chunk)
            report_progress(counters[0], counters[1])
    shutil.copystat(source, destination, follow_symlinks=False)
    if not stat.S_ISREG(source.stat(follow_symlinks=False).st_mode):
        raise ValidationError("A backup source changed type while it was being copied.")
    counters[0] += 1
    report_progress(counters[0], counters[1])


def _is_excluded(relative_path: Path, excludes: tuple[str, ...]) -> bool:
    normalized_path = relative_path.as_posix()
    for rule in excludes:
        pattern = rule.strip().lstrip("./").rstrip("/")
        if pattern and (
            fnmatch.fnmatchcase(normalized_path, pattern)
            or fnmatch.fnmatchcase(relative_path.name, pattern)
        ):
            return True
    return False


def _check_cancelled(cancellation: Event) -> None:
    if cancellation.is_set():
        raise BackupCancelledError("Backup cancellation requested.")
