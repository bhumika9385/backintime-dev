import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from backintime.api.app import create_app
from backintime.core.backup import BackupResult, ProgressCallback, SnapshotCallback
from backintime.core.config import ConfigStore
from backintime.core.exceptions import BackupCancelledError
from backintime.core.jobs import JobService
from backintime.core.profile import Profile

_ORIGIN = {"Origin": "http://127.0.0.1:5173"}


def _write_config(tmp_path: Path, *profile_ids: str) -> Path:
    profiles = []
    for profile_id in profile_ids:
        source = tmp_path / f"source-{profile_id}"
        source.mkdir()
        (source / "file.txt").write_bytes(b"data")
        profiles.append(
            {
                "profile_id": profile_id,
                "name": profile_id,
                "sources": [str(source)],
                "target": str(tmp_path / f"target-{profile_id}"),
            }
        )
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"version": 1, "profiles": profiles}), encoding="utf-8")
    return config


@pytest.fixture
def client_factory(tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def make(job_service: JobService | None = None) -> TestClient:
        app = create_app(
            config_store=ConfigStore(tmp_path / "config.json"),
            job_service=job_service,
            job_state_path=tmp_path / "state" / "jobs.json",
        )
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def _wait_for_state(client: TestClient, job_id: str, *states: str) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while True:
        job: dict[str, object] = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["state"] in states:
            return job
        if time.monotonic() > deadline:
            raise AssertionError(f"Job stayed {job['state']}")
        time.sleep(0.01)


class _Blocking:
    def __init__(self) -> None:
        self.started = Event()

    def __call__(
        self,
        profile: Profile,
        cancellation: Event,
        report_progress: ProgressCallback,
        report_snapshot: SnapshotCallback,
    ) -> BackupResult:
        self.started.set()
        while not cancellation.wait(0.01):
            pass
        raise BackupCancelledError("cancelled")


def test_backup_runs_and_reports_status(
    tmp_path: Path, client_factory: Callable[..., TestClient]
) -> None:
    _write_config(tmp_path, "home")
    client = client_factory()

    response = client.post("/api/v1/profiles/home/backups", headers=_ORIGIN)

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = _wait_for_state(client, job_id, "completed", "failed")
    assert job["state"] == "completed"
    assert job["files_copied"] == 1
    assert job["bytes_copied"] == 4
    assert isinstance(job["snapshot_id"], str)
    assert client.get("/api/v1/jobs").json()["jobs"][0]["job_id"] == job_id


def test_starting_backup_requires_allowed_origin(
    tmp_path: Path, client_factory: Callable[..., TestClient]
) -> None:
    _write_config(tmp_path, "home")
    client = client_factory()

    assert client.post("/api/v1/profiles/home/backups").status_code == 403
    assert (
        client.post(
            "/api/v1/profiles/home/backups", headers={"Origin": "http://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/jobs").json() == {"jobs": []}


def test_conflicting_backup_returns_409_and_cancel_stops_it(
    tmp_path: Path, client_factory: Callable[..., TestClient]
) -> None:
    _write_config(tmp_path, "home")
    operation = _Blocking()
    client = client_factory(JobService(tmp_path / "state" / "jobs.json", operation))

    job_id = client.post("/api/v1/profiles/home/backups", headers=_ORIGIN).json()["job_id"]
    assert operation.started.wait(5)

    conflict = client.post("/api/v1/profiles/home/backups", headers=_ORIGIN)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "JOB_CONFLICT"

    cancel = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=_ORIGIN)
    assert cancel.status_code == 200
    assert cancel.json()["logs"][-1]["message"] == "Cancellation requested."
    assert _wait_for_state(client, job_id, "cancelled")["state"] == "cancelled"


def test_unknown_profile_and_job_return_404(
    tmp_path: Path, client_factory: Callable[..., TestClient]
) -> None:
    _write_config(tmp_path, "home")
    client = client_factory()

    missing_profile = client.post("/api/v1/profiles/nope/backups", headers=_ORIGIN)
    assert missing_profile.status_code == 404
    assert missing_profile.json()["error"]["code"] == "PROFILE_NOT_FOUND"
    for response in (
        client.get("/api/v1/jobs/unknown"),
        client.post("/api/v1/jobs/unknown/cancel", headers=_ORIGIN),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_job_service_starts_lazily_and_stops_with_app(tmp_path: Path) -> None:
    _write_config(tmp_path, "home")
    state = tmp_path / "state" / "jobs.json"
    app = create_app(config_store=ConfigStore(tmp_path / "config.json"), job_state_path=state)

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        client.get("/api/v1/health")
        assert app.state.job_service is None
        assert not state.parent.exists()
        client.get("/api/v1/jobs")
        assert isinstance(app.state.job_service, JobService)

    # The lock was released on shutdown, so another service can own the state file.
    JobService(state).shutdown()


def test_second_process_cannot_run_jobs(
    tmp_path: Path, client_factory: Callable[..., TestClient]
) -> None:
    _write_config(tmp_path, "home")
    owner = JobService(tmp_path / "state" / "jobs.json")
    try:
        client = client_factory()
        response = client.get("/api/v1/jobs")
    finally:
        owner.shutdown()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "JOB_SERVICE_UNAVAILABLE"
    assert "already running background jobs" in response.json()["error"]["message"]
