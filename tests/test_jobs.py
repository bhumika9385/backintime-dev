import json
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event

import pytest

from backintime.core.backup import (
    BackupResult,
    ProgressCallback,
    SnapshotCallback,
    create_snapshot,
)
from backintime.core.exceptions import BackupCancelledError, JobConflictError, JobStoreError
from backintime.core.jobs import BackupJob, JobService
from backintime.core.profile import Profile
from backintime.core.repository import Repository


def _wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for job state.")
        time.sleep(0.01)


def _profile(tmp_path: Path, profile_id: str = "home") -> Profile:
    source = tmp_path / f"source-{profile_id}"
    (source / "nested").mkdir(parents=True, exist_ok=True)
    (source / "a.txt").write_bytes(b"a" * 10)
    (source / "nested" / "b.txt").write_bytes(b"b" * 20)
    (source / "skip.cache").write_bytes(b"c")
    return Profile(
        profile_id=profile_id,
        name=profile_id,
        sources=(source,),
        target=tmp_path / f"target-{profile_id}",
        excludes=("*.cache",),
    )


class _Blocking:
    """A backup operation that runs until released or cancelled."""

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def __call__(
        self,
        profile: Profile,
        cancellation: Event,
        report_progress: ProgressCallback,
        report_snapshot: SnapshotCallback,
    ) -> BackupResult:
        self.started.set()
        report_progress(1, 5)
        while not self.release.wait(0.01):
            if cancellation.is_set():
                raise BackupCancelledError("cancelled")
        return BackupResult("20240101-000000-001", 1, 5)


@pytest.fixture
def services(tmp_path: Path) -> Iterator[list[JobService]]:
    created: list[JobService] = []
    yield created
    for service in created:
        service.shutdown()


def _service(services: list[JobService], path: Path, operation: object | None = None) -> JobService:
    service = JobService(path) if operation is None else JobService(path, operation)  # type: ignore[arg-type]
    services.append(service)
    return service


def _finished(service: JobService, job_id: str) -> Callable[[], bool]:
    def check() -> bool:
        job = service.get(job_id)
        return job is not None and not job.active

    return check


def test_completed_backup_reports_real_outcome(tmp_path: Path, services: list[JobService]) -> None:
    service = _service(services, tmp_path / "state" / "jobs.json")
    profile = _profile(tmp_path)

    job = service.submit(profile)
    assert job.state == "queued"
    _wait_for(_finished(service, job.job_id))

    finished = service.get(job.job_id)
    assert finished is not None
    assert finished.state == "completed"
    assert (finished.files_copied, finished.bytes_copied) == (2, 30)
    assert [entry.message for entry in finished.logs] == [
        "Backup queued.",
        "Backup started.",
        "Backup completed.",
    ]
    snapshots = Repository(profile.target).list_snapshots()
    assert [str(record.snapshot_id) for record in snapshots] == [finished.snapshot_id]
    data = snapshots[0].path / "data" / "source-001"
    assert (data / "nested" / "b.txt").read_bytes() == b"b" * 20
    assert not (data / "skip.cache").exists()


def test_failed_backup_is_failed_without_exposing_details(
    tmp_path: Path, services: list[JobService], caplog: pytest.LogCaptureFixture
) -> None:
    def explode(*_: object) -> BackupResult:
        raise RuntimeError("password=hunter2 in /secret/path")

    path = tmp_path / "jobs.json"
    service = _service(services, path, explode)

    with caplog.at_level(logging.DEBUG, logger="backintime.jobs"):
        job = service.submit(_profile(tmp_path))
        _wait_for(_finished(service, job.job_id))

    failed = service.get(job.job_id)
    assert failed is not None
    assert failed.state == "failed"
    assert failed.error_message == "Backup operation failed."
    assert "hunter2" not in path.read_text(encoding="utf-8")
    assert "hunter2" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_cancelling_running_backup_never_marks_snapshot_complete(
    tmp_path: Path, services: list[JobService]
) -> None:
    def cancel_after_first_file(
        profile: Profile,
        cancellation: Event,
        report_progress: ProgressCallback,
        report_snapshot: SnapshotCallback,
    ) -> BackupResult:
        def progress(files: int, size: int) -> None:
            report_progress(files, size)
            cancellation.set()

        return create_snapshot(profile, cancellation, progress, report_snapshot)

    service = _service(services, tmp_path / "jobs.json", cancel_after_first_file)
    profile = _profile(tmp_path)

    job = service.submit(profile)
    _wait_for(_finished(service, job.job_id))

    cancelled = service.get(job.job_id)
    assert cancelled is not None
    assert cancelled.state == "cancelled"
    assert cancelled.snapshot_id is not None
    assert Repository(profile.target).list_snapshots() == []
    metadata = json.loads(
        (profile.target / cancelled.snapshot_id / "info.json").read_text(encoding="utf-8")
    )
    assert metadata["state"] == "cancelled"


def test_cancel_request_stops_running_job_at_safe_point(
    tmp_path: Path, services: list[JobService]
) -> None:
    operation = _Blocking()
    service = _service(services, tmp_path / "jobs.json", operation)
    job = service.submit(_profile(tmp_path))
    assert operation.started.wait(5)

    requested = service.cancel(job.job_id)
    assert requested is not None
    assert requested.logs[-1].message == "Cancellation requested."
    _wait_for(_finished(service, job.job_id))

    cancelled = service.get(job.job_id)
    assert cancelled is not None and cancelled.state == "cancelled"
    assert service.cancel(job.job_id) == cancelled


def test_queued_job_can_be_cancelled_before_it_starts(
    tmp_path: Path, services: list[JobService]
) -> None:
    operation = _Blocking()
    service = _service(services, tmp_path / "jobs.json", operation)
    first = service.submit(_profile(tmp_path, "first"))
    assert operation.started.wait(5)
    second = service.submit(_profile(tmp_path, "second"))

    cancelled = service.cancel(second.job_id)

    assert cancelled is not None
    assert cancelled.state == "cancelled"
    assert cancelled.logs[-1].message == "Backup cancelled before it started."
    operation.release.set()
    _wait_for(_finished(service, first.job_id))
    first_job = service.get(first.job_id)
    assert first_job is not None and first_job.state == "completed"


def test_conflicting_backup_for_same_profile_is_rejected(
    tmp_path: Path, services: list[JobService]
) -> None:
    operation = _Blocking()
    service = _service(services, tmp_path / "jobs.json", operation)
    profile = _profile(tmp_path)
    first = service.submit(profile)
    assert operation.started.wait(5)

    with pytest.raises(JobConflictError, match="already has a queued or running backup"):
        service.submit(profile)

    operation.release.set()
    _wait_for(_finished(service, first.job_id))
    assert service.submit(profile).state == "queued"


def test_invalid_profile_is_rejected_before_queueing(
    tmp_path: Path, services: list[JobService]
) -> None:
    service = _service(services, tmp_path / "jobs.json")
    profile = _profile(tmp_path)
    inside_source = Profile(
        profile_id="bad",
        name="bad",
        sources=profile.sources,
        target=profile.sources[0] / "backups",
    )

    with pytest.raises(Exception, match="target is inside source"):
        service.submit(inside_source)
    assert service.list() == ()


def test_jobs_persist_and_unfinished_jobs_fail_after_restart(
    tmp_path: Path, services: list[JobService]
) -> None:
    path = tmp_path / "jobs.json"
    operation = _Blocking()
    service = JobService(path, operation)

    job = service.submit(_profile(tmp_path))
    assert operation.started.wait(5)
    # Simulate a crash: the state file still says "running" when the process dies.
    service._process_lock.release()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["jobs"][0]["state"] == "running"

    restarted = _service(services, path)

    recovered = restarted.get(job.job_id)
    assert recovered is not None
    assert recovered.state == "failed"
    assert recovered.error_message == "The service restarted before this job finished."
    assert recovered.logs[-1].message == "Interrupted by service restart."
    assert json.loads(path.read_text(encoding="utf-8"))["jobs"][0]["state"] == "failed"
    operation.release.set()
    service._executor.shutdown(wait=True)


def test_completed_jobs_survive_restart(tmp_path: Path, services: list[JobService]) -> None:
    path = tmp_path / "jobs.json"
    service = JobService(path)
    job = service.submit(_profile(tmp_path))
    _wait_for(_finished(service, job.job_id))
    service.shutdown()

    restarted = _service(services, path)

    assert restarted.get(job.job_id) == service.get(job.job_id)
    assert [item.job_id for item in restarted.list()] == [job.job_id]


def test_only_one_process_may_own_the_job_state(tmp_path: Path, services: list[JobService]) -> None:
    path = tmp_path / "jobs.json"
    first = JobService(path)

    with pytest.raises(JobStoreError, match="already running background jobs"):
        JobService(path)

    first.shutdown()
    _service(services, path)


def test_corrupt_state_is_reported_and_releases_lock(
    tmp_path: Path, services: list[JobService]
) -> None:
    path = tmp_path / "jobs.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(JobStoreError, match="Cannot read background job state"):
        JobService(path)

    path.write_text(json.dumps({"version": 1, "jobs": [{"job_id": 1}]}), encoding="utf-8")
    with pytest.raises(JobStoreError, match="missing required fields"):
        JobService(path)

    path.unlink()
    _service(services, path)


def test_shutdown_cancels_queued_work_and_rejects_new_jobs(
    tmp_path: Path, services: list[JobService]
) -> None:
    operation = _Blocking()
    service = JobService(tmp_path / "jobs.json", operation)
    running = service.submit(_profile(tmp_path, "first"))
    assert operation.started.wait(5)
    queued = service.submit(_profile(tmp_path, "second"))

    service.shutdown()

    states = {job.job_id: job.state for job in service.list()}
    assert states == {running.job_id: "cancelled", queued.job_id: "cancelled"}
    with pytest.raises(JobStoreError, match="not accepting work"):
        service.submit(_profile(tmp_path, "third"))


def test_job_list_is_newest_first(tmp_path: Path, services: list[JobService]) -> None:
    service = _service(services, tmp_path / "jobs.json")
    first = service.submit(_profile(tmp_path, "first"))
    _wait_for(_finished(service, first.job_id))
    time.sleep(0.01)
    second = service.submit(_profile(tmp_path, "second"))
    _wait_for(_finished(service, second.job_id))

    assert [job.job_id for job in service.list()] == [second.job_id, first.job_id]
    assert all(isinstance(job, BackupJob) for job in service.list())
