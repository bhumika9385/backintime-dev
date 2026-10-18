"""Safe, read-only snapshot repository inspection."""

import heapq
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from backintime.core.exceptions import (
    RepositoryError,
    SnapshotNotFoundError,
    ValidationError,
)
from backintime.core.snapshot_id import SnapshotID
from backintime.core.validation import validate_path_within

MAX_DIRECTORY_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    """A directory named as a snapshot and its metadata health state."""

    snapshot_id: SnapshotID
    path: Path
    healthy: bool
    name: str | None = None


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    """Safe metadata for one entry in a snapshot directory."""

    name: str
    kind: Literal["directory", "file", "symlink", "other"]
    size_bytes: int
    modified_time_ns: int


@dataclass(frozen=True, slots=True)
class SnapshotDirectory:
    """One bounded page of entries from a snapshot directory."""

    path: str
    entries: tuple[SnapshotEntry, ...]
    next_cursor: str | None


class Repository:
    """Inspect snapshots stored directly below one profile repository path."""

    def __init__(self, profile_root: Path) -> None:
        self.profile_root = profile_root.expanduser().resolve(strict=False)

    def snapshot_path(self, snapshot_id: SnapshotID) -> Path:
        """Construct a snapshot path only from a validated identifier."""
        return self.profile_root / str(snapshot_id)

    def list_snapshots(self, *, include_incomplete: bool = False) -> list[SnapshotRecord]:
        """List validly named snapshot directories, excluding unhealthy entries by default."""
        if not self.profile_root.exists():
            return []
        if not self.profile_root.is_dir():
            raise RepositoryError(f"Snapshot repository is not a directory: {self.profile_root}")

        records: list[SnapshotRecord] = []
        try:
            entries = sorted(self.profile_root.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise RepositoryError(
                f"Cannot list snapshot repository '{self.profile_root}': {exc}"
            ) from exc

        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                snapshot_id = SnapshotID.parse(entry.name)
            except ValidationError:
                continue

            healthy, name = self._read_metadata(entry, snapshot_id)
            if healthy or include_incomplete:
                records.append(
                    SnapshotRecord(
                        snapshot_id=snapshot_id,
                        path=entry,
                        healthy=healthy,
                        name=name,
                    )
                )

        return sorted(records, key=lambda record: record.snapshot_id, reverse=True)

    def browse_snapshot(
        self,
        snapshot_id: SnapshotID,
        relative_path: str = "",
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> SnapshotDirectory:
        """List one bounded page without following links outside the snapshot."""
        if not 1 <= limit <= MAX_DIRECTORY_PAGE_SIZE:
            raise ValidationError(
                f"Directory page size must be between 1 and {MAX_DIRECTORY_PAGE_SIZE}."
            )
        parts = _relative_path_parts(relative_path)
        if after is not None and (
            not after or after in {".", ".."} or "/" in after or "\\" in after or "\x00" in after
        ):
            raise ValidationError("Directory cursor must be a single entry name.")

        record = next(
            (item for item in self.list_snapshots() if item.snapshot_id == snapshot_id),
            None,
        )
        if record is None:
            raise SnapshotNotFoundError(f"Snapshot '{snapshot_id}' was not found.")
        if record.path.is_symlink():
            raise RepositoryError("The selected snapshot directory changed during inspection.")

        try:
            snapshot_root = record.path.resolve(strict=True)
            snapshot_root.relative_to(self.profile_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RepositoryError("The selected snapshot directory is no longer safe.") from exc

        requested_path = snapshot_root.joinpath(*parts)
        directory = validate_path_within(requested_path, snapshot_root, allow_root=True)
        if not directory.is_dir():
            raise ValidationError("The requested path is not a directory in this snapshot.")

        try:
            with os.scandir(directory) as iterator:
                entries = heapq.nsmallest(
                    limit + 1,
                    (
                        self._entry_metadata(entry)
                        for entry in iterator
                        if after is None or entry.name > after
                    ),
                    key=lambda entry: entry.name,
                )
        except OSError as exc:
            raise RepositoryError(f"Cannot list snapshot directory: {exc}") from exc

        has_more = len(entries) > limit
        page = entries[:limit]
        return SnapshotDirectory(
            path="/".join(parts),
            entries=tuple(page),
            next_cursor=page[-1].name if has_more else None,
        )

    @staticmethod
    def _entry_metadata(entry: os.DirEntry[str]) -> SnapshotEntry:
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise RepositoryError(f"Cannot inspect snapshot entry '{entry.name}': {exc}") from exc
        if entry.is_symlink():
            kind: Literal["directory", "file", "symlink", "other"] = "symlink"
        elif stat.S_ISDIR(info.st_mode):
            kind = "directory"
        elif stat.S_ISREG(info.st_mode):
            kind = "file"
        else:
            kind = "other"
        return SnapshotEntry(
            name=entry.name,
            kind=kind,
            size_bytes=info.st_size,
            modified_time_ns=info.st_mtime_ns,
        )

    @staticmethod
    def _read_metadata(snapshot_path: Path, snapshot_id: SnapshotID) -> tuple[bool, str | None]:
        metadata_path = snapshot_path / "info.json"
        if metadata_path.is_symlink() or not metadata_path.is_file():
            return False, None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False, None
        if not isinstance(metadata, dict):
            return False, None
        if metadata.get("snapshot_id") != str(snapshot_id) or metadata.get("state") != "complete":
            return False, None
        name = metadata.get("name")
        return True, name if isinstance(name, str) else None


def _relative_path_parts(value: str) -> tuple[str, ...]:
    """Parse API paths independent of the host OS and reject traversal syntax."""
    if "\x00" in value:
        raise ValidationError("Snapshot paths must not contain null characters.")
    windows_path = PureWindowsPath(value)
    if (
        PurePosixPath(value).is_absolute()
        or windows_path.is_absolute()
        or windows_path.root
        or windows_path.drive
    ):
        raise ValidationError("Snapshot paths must be relative.")

    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValidationError("Snapshot paths must not contain parent-directory segments.")
    return tuple(part for part in parts if part not in {"", "."})
