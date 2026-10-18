"""Safe, read-only snapshot repository inspection."""

import json
from dataclasses import dataclass
from pathlib import Path

from backintime.core.exceptions import RepositoryError, ValidationError
from backintime.core.snapshot_id import SnapshotID


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    """A directory named as a snapshot and its metadata health state."""

    snapshot_id: SnapshotID
    path: Path
    healthy: bool
    name: str | None = None
    protected: bool = False


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

            healthy, name, protected = self._read_metadata(entry, snapshot_id)
            if healthy or include_incomplete:
                records.append(
                    SnapshotRecord(
                        snapshot_id=snapshot_id,
                        path=entry,
                        healthy=healthy,
                        name=name,
                        protected=protected,
                    )
                )

        return sorted(records, key=lambda record: record.snapshot_id, reverse=True)

    @staticmethod
    def _read_metadata(
        snapshot_path: Path, snapshot_id: SnapshotID
    ) -> tuple[bool, str | None, bool]:
        metadata_path = snapshot_path / "info.json"
        if metadata_path.is_symlink() or not metadata_path.is_file():
            return False, None, False
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False, None, False
        if not isinstance(metadata, dict):
            return False, None, False
        if metadata.get("snapshot_id") != str(snapshot_id) or metadata.get("state") != "complete":
            return False, None, False
        name = metadata.get("name")
        return True, name if isinstance(name, str) else None, metadata.get("protected") is True
