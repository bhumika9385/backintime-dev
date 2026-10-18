import json
import os
from pathlib import Path

import pytest

from backintime.cli.main import main
from backintime.core.diagnostics import (
    DiskUsage,
    HealthState,
    Severity,
    diagnose_repository,
)

_PLENTY = DiskUsage(total=100 * 1024**3, used=0, free=100 * 1024**3)


def _plenty(_: Path) -> DiskUsage:
    return _PLENTY


def _write_snapshot(root: Path, snapshot_id: str, metadata: object | None = None) -> Path:
    snapshot_path = root / snapshot_id
    snapshot_path.mkdir(parents=True)
    if metadata is None:
        metadata = {"snapshot_id": snapshot_id, "state": "complete"}
    (snapshot_path / "info.json").write_text(json.dumps(metadata), encoding="utf-8")
    return snapshot_path


def _snapshot_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_healthy_repository(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "20240101-000000-001")
    _write_snapshot(tmp_path, "20240102-000000-001")
    (tmp_path / "unrelated-directory").mkdir()

    report = diagnose_repository(tmp_path, disk_usage=_plenty)

    assert report.state is HealthState.HEALTHY
    assert report.findings == ()
    assert [snapshot.snapshot_id for snapshot in report.snapshots] == [
        "20240102-000000-001",
        "20240101-000000-001",
    ]
    assert report.healthy_snapshot_count == 2


@pytest.mark.parametrize(
    ("metadata", "raw", "state", "code"),
    [
        (None, None, HealthState.INCOMPLETE, "METADATA_MISSING"),
        ({"state": "running"}, None, HealthState.INCOMPLETE, "SNAPSHOT_NOT_COMPLETE"),
        ({}, None, HealthState.INCOMPLETE, "SNAPSHOT_NOT_COMPLETE"),
        (None, "{not json", HealthState.INVALID, "METADATA_MALFORMED"),
        (None, "[1, 2]", HealthState.INVALID, "METADATA_MALFORMED"),
        (None, b"\xff\xfe", HealthState.INVALID, "METADATA_MALFORMED"),
        ({"snapshot_id": "20240101-000000-002"}, None, HealthState.INVALID, "METADATA_ID_MISMATCH"),
    ],
)
def test_unhealthy_snapshots_are_classified(
    tmp_path: Path,
    metadata: dict[str, str] | None,
    raw: str | bytes | None,
    state: HealthState,
    code: str,
) -> None:
    snapshot_id = "20240101-000000-001"
    snapshot = tmp_path / snapshot_id
    snapshot.mkdir()
    if metadata is not None:
        metadata.setdefault("snapshot_id", snapshot_id)
        (snapshot / "info.json").write_text(json.dumps(metadata), encoding="utf-8")
    elif isinstance(raw, bytes):
        (snapshot / "info.json").write_bytes(raw)
    elif raw is not None:
        (snapshot / "info.json").write_text(raw, encoding="utf-8")

    report = diagnose_repository(tmp_path, disk_usage=_plenty)

    assert report.state is state
    assert report.snapshots[0].state is state
    assert report.snapshots[0].finding is not None
    assert report.snapshots[0].finding.code == code
    assert report.snapshots[0].finding.subject == snapshot_id
    assert report.healthy_snapshot_count == 0
    assert "NO_HEALTHY_SNAPSHOTS" in {finding.code for finding in report.findings}


def test_partial_snapshot_is_never_reported_healthy(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "20240101-000000-001")
    (tmp_path / "20240102-000000-001").mkdir()

    report = diagnose_repository(tmp_path, disk_usage=_plenty)

    states = {snapshot.snapshot_id: snapshot.state for snapshot in report.snapshots}
    assert states == {
        "20240101-000000-001": HealthState.HEALTHY,
        "20240102-000000-001": HealthState.INCOMPLETE,
    }
    assert report.state is HealthState.INCOMPLETE


def test_worst_snapshot_state_determines_repository_state(tmp_path: Path) -> None:
    (tmp_path / "20240101-000000-001").mkdir()
    _write_snapshot(tmp_path, "20240102-000000-001", {"snapshot_id": "other", "state": "x"})

    report = diagnose_repository(tmp_path, disk_usage=_plenty)

    assert report.state is HealthState.INVALID


def test_snapshot_file_instead_of_directory_is_invalid(tmp_path: Path) -> None:
    (tmp_path / "20240101-000000-001").write_text("not a directory", encoding="utf-8")

    report = diagnose_repository(tmp_path, disk_usage=_plenty)

    assert report.snapshots[0].state is HealthState.INVALID
    assert report.snapshots[0].finding is not None
    assert report.snapshots[0].finding.code == "SNAPSHOT_NOT_DIRECTORY"


def test_symlinked_metadata_is_not_trusted(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"snapshot_id": "20240101-000000-001", "state": "complete"}),
        encoding="utf-8",
    )
    repository = tmp_path / "repo"
    snapshot = repository / "20240101-000000-001"
    snapshot.mkdir(parents=True)
    try:
        (snapshot / "info.json").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks unavailable: {exc}")

    report = diagnose_repository(repository, disk_usage=_plenty)

    assert report.snapshots[0].state is HealthState.INVALID
    assert report.snapshots[0].finding is not None
    assert report.snapshots[0].finding.code == "METADATA_SYMLINK"


def test_missing_target_is_inaccessible(tmp_path: Path) -> None:
    report = diagnose_repository(tmp_path / "unmounted-drive", disk_usage=_plenty)

    assert report.state is HealthState.INACCESSIBLE
    assert [finding.code for finding in report.findings] == ["TARGET_MISSING"]
    assert "mount" in report.findings[0].guidance


def test_target_file_is_invalid(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("", encoding="utf-8")

    report = diagnose_repository(target, disk_usage=_plenty)

    assert report.state is HealthState.INVALID
    assert report.findings[0].code == "TARGET_NOT_DIRECTORY"


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX non-root")
def test_unreadable_target_is_inaccessible(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    target.chmod(0)
    try:
        report = diagnose_repository(target, disk_usage=_plenty)
    finally:
        target.chmod(0o700)

    assert report.state is HealthState.INACCESSIBLE
    assert report.findings[0].code == "TARGET_UNREADABLE"


def test_empty_repository_reports_no_snapshots_as_info(tmp_path: Path) -> None:
    report = diagnose_repository(tmp_path, disk_usage=_plenty)

    assert report.state is HealthState.HEALTHY
    assert [(finding.code, finding.severity) for finding in report.findings] == [
        ("NO_HEALTHY_SNAPSHOTS", Severity.INFO)
    ]


def test_low_disk_space_is_reported(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "20240101-000000-001")

    report = diagnose_repository(
        tmp_path,
        min_free_bytes=10 * 1024**2,
        disk_usage=lambda _: DiskUsage(total=100 * 1024**2, used=99 * 1024**2, free=1024**2),
    )

    assert report.state is HealthState.HEALTHY
    low_space = [finding for finding in report.findings if finding.code == "LOW_DISK_SPACE"]
    assert len(low_space) == 1
    assert low_space[0].severity is Severity.WARNING
    assert "1.0 MiB" in low_space[0].message


def test_disk_usage_failure_is_reported_not_raised(tmp_path: Path) -> None:
    def fail(_: Path) -> DiskUsage:
        raise OSError(5, "Input/output error")

    report = diagnose_repository(tmp_path, disk_usage=fail)

    assert report.disk_usage is None
    assert "DISK_USAGE_UNAVAILABLE" in {finding.code for finding in report.findings}


def test_diagnostics_do_not_modify_repository(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "20240101-000000-001")
    _write_snapshot(tmp_path, "20240102-000000-001", {"state": "failed"})
    (tmp_path / "20240103-000000-001").mkdir()
    (tmp_path / "20240104-000000-001").mkdir()
    (tmp_path / "20240104-000000-001" / "info.json").write_text("{", encoding="utf-8")
    before = _snapshot_files(tmp_path)
    directories = sorted(path.name for path in tmp_path.iterdir())

    diagnose_repository(tmp_path, disk_usage=_plenty)

    assert _snapshot_files(tmp_path) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == directories


def _write_config(path: Path, target: Path, source: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": [
                    {
                        "profile_id": "home",
                        "name": "Home",
                        "sources": [str(source)],
                        "target": str(target),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_cli_diagnose_reports_problems_with_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "target"
    (target / "20240101-000000-001").mkdir(parents=True)
    config = tmp_path / "config.json"
    _write_config(config, target, tmp_path / "source")

    status = main(["--config", str(config), "diagnose", "--min-free-mib", "0"])

    output = capsys.readouterr().out
    assert status == 2
    assert "Profile 'home': incomplete" in output
    assert "METADATA_MISSING" not in output
    assert "20240101-000000-001: Snapshot has no metadata" in output
    assert "Next step:" in output


def test_cli_diagnose_healthy_profile_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "target"
    _write_snapshot(target, "20240101-000000-001")
    config = tmp_path / "config.json"
    _write_config(config, target, tmp_path / "source")

    status = main(["--config", str(config), "diagnose", "--min-free-mib", "0"])

    assert status == 0
    assert "Snapshots: 1 healthy of 1" in capsys.readouterr().out


def test_cli_diagnose_unknown_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config.json"
    _write_config(config, tmp_path / "target", tmp_path / "source")

    status = main(["--config", str(config), "diagnose", "--profile", "missing"])

    assert status == 1
    assert "Profile not found: missing" in capsys.readouterr().err
