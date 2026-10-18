import json
import os
from pathlib import Path

import pytest

from backintime.cli.main import main
from backintime.core.exceptions import ValidationError
from backintime.core.repository import Repository, SnapshotRecord
from backintime.core.retention import Action, RetentionPolicy, plan_retention
from backintime.core.snapshot_id import SnapshotID


def _record(
    snapshot_id: str, *, healthy: bool = True, name: str | None = None, protected: bool = False
) -> SnapshotRecord:
    return SnapshotRecord(
        snapshot_id=SnapshotID(snapshot_id),
        path=Path("/repository") / snapshot_id,
        healthy=healthy,
        name=name,
        protected=protected,
    )


def _daily(*days: int, month: int = 1) -> list[SnapshotRecord]:
    return [_record(f"2024{month:02d}{day:02d}-120000-001") for day in days]


@pytest.mark.parametrize(
    "rules",
    [
        {},
        {"keep_last": 0},
        {"keep_last": -1},
        {"keep_last": True},
        {"keep_last": "3"},
        {"keep_daily": 1.5},
        {"keep_hourly": 1},
    ],
)
def test_invalid_policies_are_rejected(rules: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RetentionPolicy.from_mapping(rules)


def test_policy_must_be_an_object() -> None:
    with pytest.raises(ValidationError, match="JSON object"):
        RetentionPolicy.from_mapping([1, 2])


def test_keep_last_keeps_newest_snapshots() -> None:
    plan = plan_retention(_daily(1, 2, 3, 4, 5), RetentionPolicy(keep_last=2))

    assert plan.keep == ("20240105-120000-001", "20240104-120000-001")
    assert plan.remove == (
        "20240103-120000-001",
        "20240102-120000-001",
        "20240101-120000-001",
    )


def test_keep_daily_keeps_newest_snapshot_per_day() -> None:
    records = [
        _record("20240103-180000-001"),
        _record("20240103-090000-001"),
        _record("20240102-180000-001"),
        _record("20240102-090000-001"),
        _record("20240101-120000-001"),
    ]

    plan = plan_retention(records, RetentionPolicy(keep_daily=2))

    assert plan.keep == ("20240103-180000-001", "20240102-180000-001")


def test_period_rules_combine() -> None:
    records = [
        *_daily(15, 10, month=3),
        *_daily(20, 5, month=2),
        *_daily(25, month=1),
        _record("20231231-120000-001"),
    ]

    plan = plan_retention(records, RetentionPolicy(keep_daily=1, keep_monthly=3, keep_yearly=2))

    reasons = {entry.snapshot_id: entry.reasons for entry in plan.entries}
    assert reasons["20240315-120000-001"] == ("newest valid snapshot", "daily", "monthly", "yearly")
    assert reasons["20240220-120000-001"] == ("monthly",)
    assert reasons["20240125-120000-001"] == ("monthly",)
    assert reasons["20231231-120000-001"] == ("yearly",)
    assert plan.remove == ("20240310-120000-001", "20240205-120000-001")


def test_keep_weekly_uses_iso_weeks() -> None:
    # 2024-01-07 is a Sunday; 2024-01-08 starts ISO week 2.
    records = _daily(9, 8, 7, 1)

    plan = plan_retention(records, RetentionPolicy(keep_weekly=2))

    assert plan.keep == ("20240109-120000-001", "20240107-120000-001")


def test_protected_and_named_snapshots_are_never_removed() -> None:
    records = [
        *_daily(5, 4),
        _record("20240103-120000-001", protected=True),
        _record("20240102-120000-001", name="before upgrade"),
        _record("20240101-120000-001"),
    ]

    plan = plan_retention(records, RetentionPolicy(keep_last=1))

    reasons = {entry.snapshot_id: entry.reasons for entry in plan.entries}
    assert reasons["20240103-120000-001"] == ("protected",)
    assert reasons["20240102-120000-001"] == ("named 'before upgrade'",)
    assert plan.remove == ("20240104-120000-001", "20240101-120000-001")


def test_last_valid_snapshot_is_kept_even_if_no_rule_matches() -> None:
    records = [
        _record("20240105-120000-001", healthy=False),
        _record("20240101-120000-001"),
    ]

    plan = plan_retention(records, RetentionPolicy(keep_yearly=1))
    plan_without_period_match = plan_retention(records[1:], RetentionPolicy(keep_last=1))

    assert plan.keep == ("20240101-120000-001",)
    assert plan.remove == ()
    assert plan_without_period_match.remove == ()


def test_incomplete_snapshots_are_skipped_not_removed() -> None:
    records = [*_daily(3, 2), _record("20240101-120000-001", healthy=False)]

    plan = plan_retention(records, RetentionPolicy(keep_last=1))

    assert plan.skip == ("20240101-120000-001",)
    assert plan.remove == ("20240102-120000-001",)
    assert plan.entries[-1].action is Action.SKIP


def test_plan_is_newest_first_and_covers_every_snapshot() -> None:
    records = list(reversed(_daily(1, 2, 3)))

    plan = plan_retention(reversed(records), RetentionPolicy(keep_last=1))

    assert [entry.snapshot_id for entry in plan.entries] == [
        "20240103-120000-001",
        "20240102-120000-001",
        "20240101-120000-001",
    ]


def test_empty_repository_plan() -> None:
    plan = plan_retention([], RetentionPolicy(keep_last=1))

    assert plan.entries == ()


def _write_snapshot(root: Path, snapshot_id: str, **metadata: object) -> Path:
    path = root / snapshot_id
    path.mkdir(parents=True)
    (path / "info.json").write_text(
        json.dumps({"snapshot_id": snapshot_id, "state": "complete", **metadata}),
        encoding="utf-8",
    )
    (path / "data.txt").write_text(snapshot_id, encoding="utf-8")
    return path


def test_repository_reads_protected_flag(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "20240101-000000-001", protected=True)
    _write_snapshot(tmp_path, "20240102-000000-001", protected="yes")

    records = {str(record.snapshot_id): record for record in Repository(tmp_path).list_snapshots()}

    assert records["20240101-000000-001"].protected
    assert not records["20240102-000000-001"].protected


def _write_config(path: Path, target: Path, retention: object | None) -> None:
    profile: dict[str, object] = {
        "profile_id": "home",
        "name": "Home",
        "sources": [str(path.parent / "source")],
        "target": str(target),
    }
    if retention is not None:
        profile["retention"] = retention
    path.write_text(json.dumps({"version": 1, "profiles": [profile]}), encoding="utf-8")


def _tree(root: Path) -> list[str]:
    return sorted(os.path.relpath(path, root) for path in root.rglob("*"))


def test_cli_preview_uses_profile_rules_and_deletes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "target"
    for day in (1, 2, 3):
        _write_snapshot(target, f"2024010{day}-000000-001")
    (target / "20240104-000000-001").mkdir()
    config = tmp_path / "config.json"
    _write_config(config, target, {"keep_last": 1})
    before = _tree(target)

    status = main(["--config", str(config), "retention-preview", "--profile", "home"])

    output = capsys.readouterr().out
    assert status == 0
    assert "dry run, nothing is deleted" in output
    assert "remove 20240101-000000-001" in output
    assert "skip   20240104-000000-001" in output
    assert "1 kept, 2 would be removed, 1 skipped" in output
    assert _tree(target) == before


def test_cli_options_override_profile_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "target"
    for day in (1, 2, 3):
        _write_snapshot(target, f"2024010{day}-000000-001")
    config = tmp_path / "config.json"
    _write_config(config, target, {"keep_last": 1})

    status = main(
        ["--config", str(config), "retention-preview", "--profile", "home", "--keep-last", "3"]
    )

    assert status == 0
    assert "3 kept, 0 would be removed" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("retention", "message"),
    [
        (None, "has no retention rules"),
        ({"keep_last": -1}, "must be between 0"),
        (["keep_last"], "must be a JSON object"),
    ],
)
def test_cli_reports_missing_or_invalid_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], retention: object | None, message: str
) -> None:
    config = tmp_path / "config.json"
    _write_config(config, tmp_path / "target", retention)

    status = main(["--config", str(config), "retention-preview", "--profile", "home"])

    assert status == 1
    assert message in capsys.readouterr().err
