import json
import logging
import os
import shutil
import sys
from pathlib import Path

import pytest

from backintime.api.schemas import TransferProgressResponse, TransferStatusResponse
from backintime.core.exceptions import ValidationError
from backintime.core.rsync_output import (
    OUTPUT_ARGS,
    ChangeKind,
    Diagnostic,
    Event,
    ItemizedChange,
    Progress,
    TransferStatus,
    parse_output,
    summarize,
)
from backintime.core.rsync_runner import (
    RsyncTransferError,
    RsyncUnavailableError,
    build_command,
    check_result,
    log_result,
    run_rsync,
)

_FAKE_RSYNC = r"""
import sys, time

out, err = sys.stdout.buffer, sys.stderr.buffer
scenario = sys.argv[-1]
assert sys.argv[1:3] == ["--out-format=BIT1 %i %n", "--info=progress2"], sys.argv
if scenario == "normal":
    for piece in (b"BIT1 >f+++++++++ fi", b"rst.txt\n\r   10  50%  1.00kB/s  0:00:01 (xfr#1, to",
                  b"-chk=1/2)", b"\nBIT1 *deleting   gone\\#012name\n",
                  b"\r   20 100%  1.00kB/s  0:00:00 (xfr#2, to-chk=0/2)\n"):
        out.write(piece)
        out.flush()
        time.sleep(0.01)
    sys.exit(0)
if scenario == "noisy":
    err.write(b"warning: noisy stderr line\n" * 50000)
    out.write(b"BIT1 >f+++++++++ file\n" * 20000)
    sys.exit(0)
if scenario == "fail":
    out.write(b"BIT1 >f+++++++++ partial.txt\n")
    err.write(b'rsync: [sender] send_files failed to open "x": Permission denied (13)\n')
    sys.exit(23)
if scenario == "slow":
    out.write(b"BIT1 >f+++++++++ one\n")
    out.flush()
    time.sleep(30)
    sys.exit(0)
sys.exit(1)
"""


@pytest.fixture
def fake_rsync(tmp_path: Path) -> str:
    script = tmp_path / "fake_rsync.py"
    script.write_text(_FAKE_RSYNC, encoding="utf-8")
    if os.name == "nt":
        wrapper = tmp_path / "rsync.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        wrapper = tmp_path / "rsync"
        wrapper.write_text(f"#!{sys.executable}\n{_FAKE_RSYNC}", encoding="utf-8")
        wrapper.chmod(0o755)
    return str(wrapper)


def test_build_command_enables_output_protocol() -> None:
    assert build_command(["-a", "--delete", "/src/", "/dst/"], executable="rsync") == [
        "rsync",
        *OUTPUT_ARGS,
        "-a",
        "--delete",
        "/src/",
        "/dst/",
    ]


@pytest.mark.parametrize(
    "argument",
    [
        "--out-format=%n",
        "--log-format=%n",
        "--info=progress1",
        "--info",
        "--progress",
        "--quiet",
        "-q",
        "-P",
        "-avP",
        "-aq",
    ],
)
def test_build_command_rejects_protocol_overrides(argument: str) -> None:
    with pytest.raises(ValidationError, match="managed by BackInTime"):
        build_command(["-a", argument, "/src/", "/dst/"])


@pytest.mark.parametrize("argument", ["-a", "-avz", "--rsh=ssh -q", "--partial", "--stats"])
def test_build_command_allows_unrelated_options(argument: str) -> None:
    assert argument in build_command([argument])


def test_run_streams_events_and_returns_result(fake_rsync: str) -> None:
    seen: list[Event] = []

    result = run_rsync(["normal"], on_event=seen.append, executable=fake_rsync)

    assert result.succeeded
    assert [change.path for change in result.changes] == ["first.txt", "gone\nname"]
    assert result.changes[1].kind is ChangeKind.DELETED
    assert result.last_progress is not None and result.last_progress.percent == 100
    assert [event for event in seen if isinstance(event, Progress)][0].percent == 50
    assert [event for event in seen if isinstance(event, ItemizedChange)] == list(result.changes)


def test_large_stderr_and_stdout_do_not_deadlock(fake_rsync: str) -> None:
    result = run_rsync(["noisy"], executable=fake_rsync)

    assert result.succeeded
    assert len(result.changes) == 20000
    assert len(result.diagnostics) == 50000
    assert all(diagnostic.stream == "stderr" for diagnostic in result.diagnostics)


def test_failed_transfer_keeps_diagnostics(fake_rsync: str) -> None:
    result = run_rsync(["fail"], executable=fake_rsync)

    assert result.status is TransferStatus.FAILED
    assert result.exit_code == 23
    assert [change.path for change in result.changes] == ["partial.txt"]
    assert result.diagnostics == (
        Diagnostic(
            'rsync: [sender] send_files failed to open "x": Permission denied (13)',
            stream="stderr",
        ),
    )
    with pytest.raises(RsyncTransferError, match="failed: Partial transfer due to error") as info:
        check_result(result)
    assert info.value.result is result


def test_callback_error_stops_rsync_and_propagates(fake_rsync: str) -> None:
    def explode(event: Event) -> None:
        raise RuntimeError("status store unavailable")

    with pytest.raises(RuntimeError, match="status store unavailable"):
        run_rsync(["slow"], on_event=explode, executable=fake_rsync)


def test_missing_rsync_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(RsyncUnavailableError, match="not installed"):
        run_rsync(["-a", "/src/", "/dst/"])

    with pytest.raises(RsyncUnavailableError, match="could not be started"):
        run_rsync(["-a"], executable=str(tmp_path / "no-such-rsync"))


def test_check_result_returns_successful_result() -> None:
    result = summarize(0, [])

    assert check_result(result) is result


def test_log_result_records_every_diagnostic(caplog: pytest.LogCaptureFixture) -> None:
    result = summarize(
        24,
        parse_output(b"BIT1 >f+++++++++ a\nsent 10 bytes  received 20 bytes\n"),
        b'file has vanished: "/src/b"\n',
    )

    with caplog.at_level(logging.INFO, logger="backintime.rsync"):
        log_result(result)

    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (
            logging.WARNING,
            "rsync partial (exit 24): Partial transfer due to vanished source files. "
            "1 change(s), 2 diagnostic line(s).",
        ),
        (logging.INFO, "rsync stdout: sent 10 bytes  received 20 bytes"),
        (logging.WARNING, 'rsync stderr: file has vanished: "/src/b"'),
    ]


def test_status_model_for_successful_transfer() -> None:
    result = summarize(
        0,
        parse_output(
            b"BIT1 >f+++++++++ a\nBIT1 cd+++++++++ dir/\nBIT1 *deleting   old\n"
            b"\r  4,096 100%  1.00kB/s  0:00:00 (xfr#1, to-chk=0/3)\n"
        ),
    )

    status = TransferStatusResponse.from_result(result)

    assert status.model_dump() == {
        "status": "succeeded",
        "succeeded": True,
        "exit_code": 0,
        "message": "Success.",
        "progress": {
            "indeterminate": False,
            "percent": 100,
            "bytes_transferred": 4096,
            "files_remaining": 0,
            "files_total": 3,
        },
        "changes": {
            "total": 3,
            "by_kind": {
                "sent": 0,
                "received": 1,
                "created": 1,
                "hard_linked": 0,
                "attributes": 0,
                "deleted": 1,
                "message": 0,
            },
        },
        "diagnostic_count": 0,
    }


def test_status_model_never_fabricates_progress() -> None:
    scanning = summarize(
        11,
        parse_output(b"\r  1,024  40%  1.00kB/s  0:00:09 (xfr#1, ir-chk=10/90)\n"),
        b"rsync: write failed: No space left on device (28)\n",
    )

    status = TransferStatusResponse.from_result(scanning)

    assert not status.succeeded
    assert status.status == "failed"
    assert status.progress.indeterminate
    assert status.progress.percent is None
    assert status.progress.bytes_transferred == 1024
    assert status.diagnostic_count == 1
    assert "No space left" not in json.dumps(status.model_dump())
    assert TransferProgressResponse.from_progress(None).model_dump() == {
        "indeterminate": True,
        "percent": None,
        "bytes_transferred": 0,
        "files_remaining": None,
        "files_total": None,
    }


@pytest.mark.skipif(
    shutil.which("rsync") is None or sys.platform == "win32",
    reason="requires rsync on a POSIX filesystem",
)
def test_real_rsync_run(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "a.txt").write_bytes(b"a" * 4096)
    (source / "nested" / "line\nbreak.txt").write_bytes(b"b")
    seen: list[Event] = []

    result = run_rsync(["-a", f"{source}/", f"{tmp_path / 'dest'}/"], on_event=seen.append)

    assert check_result(result).succeeded
    files = {change.path for change in result.changes if change.file_type == "file"}
    assert files == {"a.txt", "nested/line\nbreak.txt"}
    assert any(isinstance(event, Progress) for event in seen)
    assert TransferStatusResponse.from_result(result).progress.bytes_transferred >= 4097

    missing = run_rsync(["-a", f"{tmp_path / 'missing'}/", f"{tmp_path / 'dest'}/"])
    with pytest.raises(RsyncTransferError):
        check_result(missing)
    assert missing.exit_code == 23
