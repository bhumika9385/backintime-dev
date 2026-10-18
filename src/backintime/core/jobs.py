"""Single-process background backup jobs with durable status records.

One :class:`JobService` runs at most one backup at a time on a worker thread.
Job records are written atomically to a JSON state file after every change, so
status survives an API restart. A job that was queued or running when the
process stopped is marked ``failed`` on the next start, because its outcome is
unknown; its snapshot was never marked ``complete``.

Only one process may own a state file: the service holds an exclusive OS lock
on ``<state file>.lock`` for its lifetime.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, RLock
from typing import IO, Literal, cast
from uuid import uuid4

from backintime.core.backup import (
    BackupResult,
    ProgressCallback,
    SnapshotCallback,
    create_snapshot,
)
from backintime.core.exceptions import (
    BackupCancelledError,
    JobConflictError,
    JobStoreError,
)
from backintime.core.profile import Profile

JobState = Literal["queued", "running", "completed", "failed", "cancelled"]
_JOB_STATES = frozenset({"queued", "running", "completed", "failed", "cancelled"})
_ACTIVE_STATES = frozenset({"queued", "running"})
_MAX_LOG_ENTRIES = 100
_logger = logging.getLogger("backintime.jobs")

BackupOperation = Callable[[Profile, Event, ProgressCallback, SnapshotCallback], BackupResult]


@dataclass(frozen=True, slots=True)
class JobLogEntry:
    """A non-sensitive job lifecycle message."""

    timestamp: str
    message: str


@dataclass(frozen=True, slots=True)
class BackupJob:
    """Persisted public status for one backup operation."""

    job_id: str
    profile_id: str
    state: JobState
    created_at: str
    updated_at: str
    files_copied: int = 0
    bytes_copied: int = 0
    snapshot_id: str | None = None
    error_message: str | None = None
    logs: tuple[JobLogEntry, ...] = ()

    @property
    def active(self) -> bool:
        return self.state in _ACTIVE_STATES


class JobService:
    """Run one backup at a time and recover unfinished records after restart."""

    def __init__(
        self,
        state_path: Path,
        operation: BackupOperation = create_snapshot,
    ) -> None:
        self.state_path = state_path.expanduser()
        self._operation = operation
        self._lock = RLock()
        self._process_lock = _ProcessLock(self.state_path.with_name(f"{self.state_path.name}.lock"))
        try:
            self._jobs = self._load_jobs()
        except BaseException:
            self._process_lock.release()
            raise
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backintime-backup")
        self._tokens: dict[str, Event] = {}
        self._futures: dict[str, Future[None]] = {}
        self._closed = False
        self._recover_unfinished_jobs()

    @staticmethod
    def default_path() -> Path:
        """Return the XDG state path for job records, with the standard home fallback."""
        state_home = os.environ.get("XDG_STATE_HOME")
        base = Path(state_home) if state_home else Path.home() / ".local" / "state"
        return base / "backintime" / "jobs.json"

    def submit(self, profile: Profile) -> BackupJob:
        """Queue a validated profile backup and return its initial status.

        Raises :class:`JobConflictError` if the profile already has a queued or
        running job, so concurrent requests cannot start conflicting backups.
        """
        profile.validate()
        now = _now()
        job_id = uuid4().hex
        job = BackupJob(
            job_id=job_id,
            profile_id=profile.profile_id,
            state="queued",
            created_at=now,
            updated_at=now,
            logs=(JobLogEntry(timestamp=now, message="Backup queued."),),
        )
        cancellation = Event()

        with self._lock:
            if self._closed:
                raise JobStoreError("The background job service is not accepting work.")
            if any(
                item.active and item.profile_id == profile.profile_id
                for item in self._jobs.values()
            ):
                raise JobConflictError(
                    f"Profile '{profile.profile_id}' already has a queued or running backup."
                )
            self._jobs[job_id] = job
            self._tokens[job_id] = cancellation
            self._persist_locked()
            try:
                future = self._executor.submit(self._run_job, job_id, profile, cancellation)
            except RuntimeError as exc:
                self._set_job_locked(
                    job,
                    state="failed",
                    message="Backup could not be queued.",
                    error_message="Backup operation failed.",
                )
                raise JobStoreError("The background job service is not accepting work.") from exc
            self._futures[job_id] = future
        return job

    def get(self, job_id: str) -> BackupJob | None:
        """Return a stable copy of a job record, if it exists."""
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> tuple[BackupJob, ...]:
        """Return jobs newest first."""
        with self._lock:
            return tuple(sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True))

    def cancel(self, job_id: str) -> BackupJob | None:
        """Request cancellation or cancel a queued job before it starts."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state in {"completed", "failed", "cancelled"}:
                return job

            cancellation = self._tokens[job_id]
            if cancellation.is_set():
                return job
            cancellation.set()

            future = self._futures.get(job_id)
            if job.state == "queued" and future is not None and future.cancel():
                return self._set_job_locked(
                    job,
                    state="cancelled",
                    message="Backup cancelled before it started.",
                )
            return self._append_log_locked(job, "Cancellation requested.")

    def shutdown(self) -> None:
        """Cancel pending work, wait for the running operation's safe point, and unlock."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for job_id, job in tuple(self._jobs.items()):
                if job.state not in {"queued", "running"}:
                    continue
                cancellation = self._tokens.get(job_id)
                if cancellation is not None:
                    cancellation.set()
                future = self._futures.get(job_id)
                if job.state == "queued" and future is not None and future.cancel():
                    self._set_job_locked(
                        job,
                        state="cancelled",
                        message="Backup cancelled during service shutdown.",
                    )
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._process_lock.release()

    def _run_job(self, job_id: str, profile: Profile, cancellation: Event) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.state != "queued":
                return
            if cancellation.is_set():
                self._set_job_locked(
                    job,
                    state="cancelled",
                    message="Backup cancelled before it started.",
                )
                return
            job = self._set_job_locked(job, state="running", message="Backup started.")

        try:
            result = self._operation(
                profile,
                cancellation,
                self._report_progress(job_id),
                self._report_snapshot(job_id),
            )
        except BackupCancelledError:
            self._finish_job(
                job_id,
                state="cancelled",
                message="Backup cancelled at a safe point.",
            )
        except Exception as exc:
            _logger.error("Backup job %s failed (%s).", job_id, type(exc).__name__)
            self._finish_job(
                job_id,
                state="failed",
                message="Backup failed.",
                error_message="Backup operation failed.",
            )
        else:
            if not isinstance(result, BackupResult):
                _logger.error("Backup job %s returned an invalid result.", job_id)
                self._finish_job(
                    job_id,
                    state="failed",
                    message="Backup failed.",
                    error_message="Backup operation failed.",
                )
                return
            self._finish_job(
                job_id,
                state="completed",
                message="Backup completed.",
                result=result,
            )

    def _report_progress(self, job_id: str) -> ProgressCallback:
        def update(files_copied: int, bytes_copied: int) -> None:
            with self._lock:
                job = self._jobs[job_id]
                if job.state != "running":
                    return
                self._jobs[job_id] = replace(
                    job,
                    files_copied=files_copied,
                    bytes_copied=bytes_copied,
                    updated_at=_now(),
                )
                self._persist_locked()

        return update

    def _report_snapshot(self, job_id: str) -> SnapshotCallback:
        def update(snapshot_id: str) -> None:
            with self._lock:
                job = self._jobs[job_id]
                if job.state != "running":
                    return
                self._jobs[job_id] = replace(job, snapshot_id=snapshot_id, updated_at=_now())
                self._persist_locked()

        return update

    def _finish_job(
        self,
        job_id: str,
        *,
        state: Literal["completed", "failed", "cancelled"],
        message: str,
        error_message: str | None = None,
        result: BackupResult | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            self._set_job_locked(
                job,
                state=state,
                message=message,
                error_message=error_message,
                result=result,
            )
            self._tokens.pop(job_id, None)
            self._futures.pop(job_id, None)

    def _set_job_locked(
        self,
        job: BackupJob,
        *,
        state: JobState,
        message: str,
        error_message: str | None = None,
        result: BackupResult | None = None,
    ) -> BackupJob:
        now = _now()
        updated = replace(
            job,
            state=state,
            updated_at=now,
            files_copied=result.files_copied if result is not None else job.files_copied,
            bytes_copied=result.bytes_copied if result is not None else job.bytes_copied,
            snapshot_id=result.snapshot_id if result is not None else job.snapshot_id,
            error_message=error_message,
            logs=_append_log(job.logs, message, now),
        )
        self._jobs[job.job_id] = updated
        self._persist_locked()
        return updated

    def _append_log_locked(self, job: BackupJob, message: str) -> BackupJob:
        now = _now()
        updated = replace(
            job,
            updated_at=now,
            logs=_append_log(job.logs, message, now),
        )
        self._jobs[job.job_id] = updated
        self._persist_locked()
        return updated

    def _recover_unfinished_jobs(self) -> None:
        changed = False
        for job_id, job in tuple(self._jobs.items()):
            if job.state not in {"queued", "running"}:
                continue
            now = _now()
            self._jobs[job_id] = replace(
                job,
                state="failed",
                updated_at=now,
                error_message="The service restarted before this job finished.",
                logs=_append_log(job.logs, "Interrupted by service restart.", now),
            )
            changed = True
        if changed:
            with self._lock:
                self._persist_locked()

    def _load_jobs(self) -> dict[str, BackupJob]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobStoreError("Cannot read background job state.") from exc

        if (
            not isinstance(raw, dict)
            or raw.get("version") != 1
            or not isinstance(raw.get("jobs"), list)
        ):
            raise JobStoreError("Background job state has an unsupported format.")

        jobs: dict[str, BackupJob] = {}
        for item in raw["jobs"]:
            job = _deserialize_job(item)
            if job.job_id in jobs:
                raise JobStoreError("Background job state contains duplicate job IDs.")
            jobs[job.job_id] = job
        return jobs

    def _persist_locked(self) -> None:
        payload = {
            "version": 1,
            "jobs": [_serialize_job(job) for job in self._jobs.values()],
        }
        temporary_path: Path | None = None
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.state_path.name}.",
                suffix=".tmp",
                dir=self.state_path.parent,
            )
            temporary_path = Path(temporary_name)
            if os.name != "nt":
                os.chmod(temporary_path, 0o600)
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.state_path)
        except (OSError, TypeError, ValueError) as exc:
            raise JobStoreError("Cannot persist background job state.") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def _append_log(
    current: tuple[JobLogEntry, ...],
    message: str,
    timestamp: str,
) -> tuple[JobLogEntry, ...]:
    return (*current, JobLogEntry(timestamp=timestamp, message=message))[-_MAX_LOG_ENTRIES:]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_job(job: BackupJob) -> dict[str, object]:
    return {
        **asdict(job),
        "logs": [asdict(entry) for entry in job.logs],
    }


def _deserialize_job(value: object) -> BackupJob:
    if not isinstance(value, dict):
        raise JobStoreError("Background job record is invalid.")
    string_fields = ("job_id", "profile_id", "created_at", "updated_at")
    if any(not isinstance(value.get(field), str) for field in string_fields):
        raise JobStoreError("Background job record is missing required fields.")
    state = value.get("state")
    if state not in _JOB_STATES:
        raise JobStoreError("Background job record has an invalid state.")
    files_copied = value.get("files_copied", 0)
    bytes_copied = value.get("bytes_copied", 0)
    if type(files_copied) is not int or type(bytes_copied) is not int:
        raise JobStoreError("Background job progress is invalid.")
    snapshot_id = value.get("snapshot_id")
    error_message = value.get("error_message")
    if snapshot_id is not None and not isinstance(snapshot_id, str):
        raise JobStoreError("Background job snapshot ID is invalid.")
    if error_message is not None and not isinstance(error_message, str):
        raise JobStoreError("Background job error message is invalid.")
    raw_logs = value.get("logs", [])
    if not isinstance(raw_logs, list):
        raise JobStoreError("Background job logs are invalid.")
    logs: list[JobLogEntry] = []
    for entry in raw_logs:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("timestamp"), str)
            or not isinstance(entry.get("message"), str)
        ):
            raise JobStoreError("Background job log entry is invalid.")
        logs.append(JobLogEntry(timestamp=entry["timestamp"], message=entry["message"]))
    return BackupJob(
        job_id=value["job_id"],
        profile_id=value["profile_id"],
        state=cast(JobState, state),
        created_at=value["created_at"],
        updated_at=value["updated_at"],
        files_copied=files_copied,
        bytes_copied=bytes_copied,
        snapshot_id=snapshot_id,
        error_message=error_message,
        logs=tuple(logs),
    )


class _ProcessLock:
    """Exclusive, non-blocking OS lock so only one process runs jobs for a state file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open("a+b")
        except OSError as exc:
            raise JobStoreError("Cannot open the background job lock file.") from exc
        try:
            _lock_file(stream)
        except OSError as exc:
            stream.close()
            raise JobStoreError(
                "Another BackInTime service process is already running background jobs."
            ) from exc
        self._stream: IO[bytes] | None = stream

    def release(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            _unlock_file(stream)
        finally:
            stream.close()


if sys.platform == "win32":
    import msvcrt

    def _lock_file(stream: IO[bytes]) -> None:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(stream: IO[bytes]) -> None:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(stream: IO[bytes]) -> None:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(stream: IO[bytes]) -> None:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
