import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backintime.core.rsync_output import (
    OUTPUT_ARGS,
    ChangeKind,
    Diagnostic,
    ItemizedChange,
    Progress,
    RsyncOutputParser,
    TransferStatus,
    decode_path,
    parse_output,
    parse_record,
    summarize,
)

# Captured from rsync 3.5.0 run with OUTPUT_ARGS (abridged).
NORMAL_OUTPUT = (
    b"BIT1 cd+++++++++ ./\n"
    b"\r              1   9%    0.00kB/s    0:00:00"
    b"\r              1   9%    0.00kB/s    0:00:00 (xfr#1, to-chk=6/8)\n"
    b"BIT1 >f+++++++++ docs/report final.txt\n"
    b"BIT1 .d..t...... docs/\n"
    b"BIT1 cL+++++++++ docs/latest -> x\n"
    b"BIT1 hf+++++++++ copy.txt\n"
    b"BIT1 *deleting   old.txt\n"
    b"\r          4,096  45%    3.91kB/s    0:00:00 (xfr#5, to-chk=0/8)\n"
)


def _changes(events: list[object]) -> list[ItemizedChange]:
    return [event for event in events if isinstance(event, ItemizedChange)]


def test_parses_normal_output() -> None:
    events = parse_output(NORMAL_OUTPUT)

    changes = _changes(events)
    assert [(change.kind, change.file_type, change.path) for change in changes] == [
        (ChangeKind.CREATED, "directory", "./"),
        (ChangeKind.RECEIVED, "file", "docs/report final.txt"),
        (ChangeKind.ATTRIBUTES, "directory", "docs/"),
        (ChangeKind.CREATED, "symlink", "docs/latest -> x"),
        (ChangeKind.HARD_LINKED, "file", "copy.txt"),
        (ChangeKind.DELETED, None, "old.txt"),
    ]
    assert changes[-1].is_deletion
    assert not any(isinstance(event, Diagnostic) for event in events)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"new\\#012line", "new\nline"),
        (b"carriage\\#015return", "carriage\rreturn"),
        (b"tab\there", "tab\there"),
        (b"uni-\xc3\xa9.txt", "uni-é.txt"),
        (b"uni-\\#303\\#251.txt", "uni-é.txt"),
        (b"back\\#134#123slash", "back\\#123slash"),
        (b"plain\\backslash", "plain\\backslash"),
        (b"  leading and trailing  ", "  leading and trailing  "),
    ],
)
def test_decodes_escaped_names(raw: bytes, expected: str) -> None:
    assert decode_path(raw) == expected


def test_invalid_utf8_names_round_trip() -> None:
    name = decode_path(b"bad\\#377byte")

    assert name.encode("utf-8", errors="surrogateescape") == b"bad\xffbyte"


def test_tab_in_name_does_not_split_record() -> None:
    (change,) = _changes(parse_output(b"BIT1 *deleting   tab\there\n"))

    assert change.kind is ChangeKind.DELETED
    assert change.path == "tab\there"


def test_progress_is_indeterminate_until_total_is_known() -> None:
    events = parse_output(
        b"\r    1,024   3%  1.00MB/s  0:00:09"
        b"\r    2,048   7%  1.00MB/s  0:00:08 (xfr#1, ir-chk=1000/1200)"
        b"\r    4,096  50%  1.00MB/s  0:00:01 (xfr#2, to-chk=5/10)\n"
    )

    progress = [event for event in events if isinstance(event, Progress)]
    assert [(item.bytes_transferred, item.percent) for item in progress] == [
        (1024, None),
        (2048, None),
        (4096, 50),
    ]
    assert progress[0].indeterminate and progress[1].indeterminate
    assert (progress[2].files_remaining, progress[2].files_total) == (5, 10)


def test_impossible_percentage_is_not_reported() -> None:
    event = parse_record(b"  10  250%  1.00kB/s  0:00:00 (xfr#1, to-chk=0/1)")

    assert isinstance(event, Progress)
    assert event.percent is None


@pytest.mark.parametrize(
    "record",
    [
        b"BIT1 >f+++",
        b"BIT1 ?f+++++++++ unknown-update-type",
        b"BIT1 >f+++++++++",
        b"BIT1 \xff\xfe+++++++++ name",
        b'rsync: [sender] send_files failed to open "/src/secret": Permission denied (13)',
        b"sent 1,234 bytes  received 56 bytes  2,580.00 bytes/sec",
        b"   12%  oops",
    ],
)
def test_malformed_and_unexpected_output_is_kept_as_diagnostic(record: bytes) -> None:
    event = parse_record(record)

    assert isinstance(event, Diagnostic)
    assert event.text == record.decode("utf-8", errors="replace")


def test_chunked_input_matches_complete_input() -> None:
    parser = RsyncOutputParser()
    events = []
    for index in range(len(NORMAL_OUTPUT)):
        events.extend(parser.feed(NORMAL_OUTPUT[index : index + 1]))
    events.extend(parser.close())

    assert events == parse_output(NORMAL_OUTPUT)


def test_unterminated_final_record_is_parsed_on_close() -> None:
    parser = RsyncOutputParser()

    assert parser.feed(b"BIT1 >f+++++++++ last") == []
    (change,) = parser.close()
    assert isinstance(change, ItemizedChange)
    assert change.path == "last"


def test_success_requires_zero_exit_status() -> None:
    result = summarize(0, parse_output(NORMAL_OUTPUT))

    assert result.succeeded
    assert result.status is TransferStatus.SUCCEEDED
    assert len(result.changes) == 6
    assert result.last_progress is not None and result.last_progress.percent == 45


@pytest.mark.parametrize(
    ("exit_code", "status", "message"),
    [
        (23, TransferStatus.FAILED, "Partial transfer due to error."),
        (24, TransferStatus.PARTIAL, "Partial transfer due to vanished source files."),
        (11, TransferStatus.FAILED, "Error in file I/O."),
        (99, TransferStatus.FAILED, "rsync exited with unknown status 99."),
        (-9, TransferStatus.FAILED, "rsync was terminated by signal 9."),
    ],
)
def test_failures_are_not_hidden_by_clean_output(
    exit_code: int, status: TransferStatus, message: str
) -> None:
    result = summarize(exit_code, parse_output(NORMAL_OUTPUT))

    assert result.status is status
    assert not result.succeeded
    assert result.message == message


def test_malformed_output_does_not_change_exit_status_outcome() -> None:
    result = summarize(12, parse_output(b"\x00\xff garbage\nBIT1 broken\n"))

    assert result.status is TransferStatus.FAILED
    assert [diagnostic.text for diagnostic in result.diagnostics] == [
        "\x00� garbage",
        "BIT1 broken",
    ]


def test_stderr_is_preserved_for_logs() -> None:
    stderr = b"rsync: write failed: No space left on device (28)\nrsync error: code 11\n"

    result = summarize(11, [], stderr)

    assert [(item.stream, item.text) for item in result.diagnostics] == [
        ("stderr", "rsync: write failed: No space left on device (28)"),
        ("stderr", "rsync error: code 11"),
    ]


_UNUSUAL_NAMES = [
    "plain.txt",
    "with space.txt",
    "new\nline",
    "carriage\rreturn",
    "tab\there",
    "uni-é-日本.txt",
    "back\\#123slash",
    "BIT1 >f+++++++++ fake-record",
    b"bad\xffbyte".decode("utf-8", errors="surrogateescape"),
]


@pytest.mark.skipif(
    shutil.which("rsync") is None or sys.platform == "win32",
    reason="requires rsync on a POSIX filesystem",
)
def test_real_rsync_reports_unusual_names_exactly(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    for name in _UNUSUAL_NAMES:
        (source / name).write_bytes(b"x")

    completed = subprocess.run(
        ["rsync", "-a", *OUTPUT_ARGS, f"{source}/", f"{destination}/"],
        capture_output=True,
        check=False,
    )
    result = summarize(completed.returncode, parse_output(completed.stdout), completed.stderr)

    assert result.succeeded, result.diagnostics
    files = {change.path for change in result.changes if change.file_type == "file"}
    assert files == set(_UNUSUAL_NAMES)
    assert all(diagnostic.stream == "stdout" for diagnostic in result.diagnostics)

    (source / "tab\there").unlink()
    completed = subprocess.run(
        ["rsync", "-a", "--delete", *OUTPUT_ARGS, f"{source}/", f"{destination}/"],
        capture_output=True,
        check=False,
    )
    result = summarize(completed.returncode, parse_output(completed.stdout), completed.stderr)

    assert result.succeeded
    assert [change.path for change in result.changes if change.is_deletion] == ["tab\there"]


@pytest.mark.skipif(shutil.which("rsync") is None, reason="requires rsync")
def test_real_rsync_failure_is_reported_as_failure(tmp_path: Path) -> None:
    completed = subprocess.run(
        ["rsync", "-a", *OUTPUT_ARGS, f"{tmp_path / 'missing'}/", f"{tmp_path / 'out'}/"],
        capture_output=True,
        check=False,
    )
    result = summarize(completed.returncode, parse_output(completed.stdout), completed.stderr)

    assert result.status is TransferStatus.FAILED
    assert result.exit_code == 23
    assert any(diagnostic.stream == "stderr" for diagnostic in result.diagnostics)
