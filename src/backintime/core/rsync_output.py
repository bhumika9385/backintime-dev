"""Structured parsing of rsync output.

rsync is run with :data:`OUTPUT_ARGS`, so every transferred or deleted path is
printed as one record::

    BIT1 <11-character itemize string> <escaped path>

rsync escapes control characters in names (including newline and carriage
return) and a literal backslash before ``#`` as ``\\#ooo`` octal sequences.
Records are separated by ``\\n`` and progress updates by ``\\r``, so names can
be decoded losslessly even when they contain tabs, newlines, or bytes that are
not valid UTF-8.

Anything that is not a change record or a progress update is kept verbatim as
a diagnostic. The outcome of a transfer is decided only by rsync's exit status;
parsing problems can never turn a failed transfer into a successful one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

RECORD_PREFIX = "BIT1 "
OUTPUT_ARGS = (f"--out-format={RECORD_PREFIX}%i %n", "--info=progress2")

_ITEMIZE_WIDTH = 11
_ESCAPE = re.compile(rb"\\#([0-7]{3})")
_PROGRESS = re.compile(
    r"\s*(?P<bytes>[\d,.]+)\s+(?P<percent>\d{1,3})%\s+(?P<rate>\S+)\s+(?P<eta>\d+:\d{2}:\d{2})"
    r"(?:\s+\((?P<detail>[^)]*)\))?\s*"
)
_TO_CHECK = re.compile(r"\bto-chk=(\d+)/(\d+)")


class ChangeKind(str, Enum):
    """What happened to a path, derived from the first itemize character."""

    SENT = "sent"
    RECEIVED = "received"
    CREATED = "created"
    HARD_LINKED = "hard_linked"
    ATTRIBUTES = "attributes"
    DELETED = "deleted"
    MESSAGE = "message"


_UPDATE_TYPES = {
    "<": ChangeKind.SENT,
    ">": ChangeKind.RECEIVED,
    "c": ChangeKind.CREATED,
    "h": ChangeKind.HARD_LINKED,
    ".": ChangeKind.ATTRIBUTES,
    "*": ChangeKind.MESSAGE,
}
_FILE_TYPES = {"f": "file", "d": "directory", "L": "symlink", "D": "device", "S": "special"}


@dataclass(frozen=True, slots=True)
class ItemizedChange:
    """One path reported by rsync."""

    kind: ChangeKind
    itemize: str
    path: str
    raw_path: bytes
    file_type: str | None

    @property
    def is_deletion(self) -> bool:
        return self.kind is ChangeKind.DELETED


@dataclass(frozen=True, slots=True)
class Progress:
    """Overall transfer progress.

    ``percent`` is only set when rsync knows the total number of files to check
    (``to-chk``). While rsync is still scanning (``ir-chk`` or no counter) the
    total is unknown, and progress is reported as indeterminate.
    """

    bytes_transferred: int
    percent: int | None
    rate: str
    files_remaining: int | None = None
    files_total: int | None = None

    @property
    def indeterminate(self) -> bool:
        return self.percent is None


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Output that is not a change record or progress update, kept verbatim."""

    text: str
    stream: str = "stdout"


Event = ItemizedChange | Progress | Diagnostic


class TransferStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


# Exit codes documented in rsync(1).
_EXIT_MESSAGES = {
    0: "Success.",
    1: "Syntax or usage error.",
    2: "Protocol incompatibility.",
    3: "Errors selecting input/output files or directories.",
    4: "Requested action not supported.",
    5: "Error starting client-server protocol.",
    6: "Daemon unable to append to log file.",
    10: "Error in socket I/O.",
    11: "Error in file I/O.",
    12: "Error in rsync protocol data stream.",
    13: "Errors with program diagnostics.",
    14: "Error in IPC code.",
    20: "Received SIGUSR1 or SIGINT.",
    21: "Some error returned by waitpid().",
    22: "Error allocating core memory buffers.",
    23: "Partial transfer due to error.",
    24: "Partial transfer due to vanished source files.",
    25: "The --max-delete limit stopped deletions.",
    30: "Timeout in data send/receive.",
    35: "Timeout waiting for daemon connection.",
}


@dataclass(frozen=True, slots=True)
class TransferResult:
    """Outcome of one rsync run, with all diagnostics preserved for logs."""

    status: TransferStatus
    exit_code: int
    message: str
    changes: tuple[ItemizedChange, ...]
    diagnostics: tuple[Diagnostic, ...]
    last_progress: Progress | None

    @property
    def succeeded(self) -> bool:
        return self.status is TransferStatus.SUCCEEDED


def decode_path(raw: bytes) -> str:
    """Decode an rsync-escaped name; undecodable bytes are kept as surrogates."""
    unescaped = _ESCAPE.sub(lambda match: bytes([int(match.group(1), 8)]), raw)
    return unescaped.decode("utf-8", errors="surrogateescape")


def parse_record(record: bytes) -> Event | None:
    """Parse one ``\\n``- or ``\\r``-delimited record; blank records yield ``None``."""
    if not record.strip():
        return None
    prefix = RECORD_PREFIX.encode("ascii")
    if record.startswith(prefix):
        change = _parse_change(record[len(prefix) :])
        if change is not None:
            return change
    else:
        text = record.decode("utf-8", errors="replace")
        progress = _parse_progress(text)
        if progress is not None:
            return progress
    return Diagnostic(record.decode("utf-8", errors="replace"))


def _parse_change(body: bytes) -> ItemizedChange | None:
    if len(body) < _ITEMIZE_WIDTH + 2 or body[_ITEMIZE_WIDTH : _ITEMIZE_WIDTH + 1] != b" ":
        return None
    try:
        itemize = body[:_ITEMIZE_WIDTH].decode("ascii")
    except UnicodeDecodeError:
        return None
    raw_path = body[_ITEMIZE_WIDTH + 1 :]
    if itemize.startswith("*deleting"):
        kind = ChangeKind.DELETED
    elif itemize[0] in _UPDATE_TYPES:
        kind = _UPDATE_TYPES[itemize[0]]
    else:
        return None
    file_type = None if kind is ChangeKind.DELETED else _FILE_TYPES.get(itemize[1])
    return ItemizedChange(
        kind=kind,
        itemize=itemize,
        path=decode_path(raw_path),
        raw_path=raw_path,
        file_type=file_type,
    )


def _parse_progress(text: str) -> Progress | None:
    match = _PROGRESS.fullmatch(text)
    if match is None:
        return None
    try:
        transferred = int(match.group("bytes").replace(",", "").replace(".", ""))
    except ValueError:
        return None
    percent = int(match.group("percent"))
    detail = match.group("detail") or ""
    counter = _TO_CHECK.search(detail)
    if counter is None or percent > 100:
        return Progress(bytes_transferred=transferred, percent=None, rate=match.group("rate"))
    remaining, total = int(counter.group(1)), int(counter.group(2))
    return Progress(
        bytes_transferred=transferred,
        percent=percent,
        rate=match.group("rate"),
        files_remaining=remaining,
        files_total=total,
    )


class RsyncOutputParser:
    """Incrementally split rsync stdout into records and parse them.

    Data may be fed in arbitrary chunks; a record split across chunks is held
    until its terminator arrives or :meth:`close` is called.
    """

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> list[Event]:
        self._buffer += data
        *records, self._buffer = re.split(rb"[\r\n]", self._buffer)
        return [event for record in records if (event := parse_record(record)) is not None]

    def close(self) -> list[Event]:
        record, self._buffer = self._buffer, b""
        event = parse_record(record)
        return [] if event is None else [event]


def parse_output(stdout: bytes) -> list[Event]:
    """Parse complete rsync stdout."""
    parser = RsyncOutputParser()
    return parser.feed(stdout) + parser.close()


def summarize(exit_code: int, events: Iterable[Event], stderr: bytes = b"") -> TransferResult:
    """Combine rsync's exit status with parsed output.

    Success is decided by the exit status alone: 0 succeeds, 24 (source files
    vanished during transfer) is partial, everything else fails.
    """
    changes: list[ItemizedChange] = []
    diagnostics: list[Diagnostic] = []
    last_progress: Progress | None = None
    for event in events:
        if isinstance(event, ItemizedChange):
            changes.append(event)
        elif isinstance(event, Progress):
            last_progress = event
        else:
            diagnostics.append(event)
    for line in stderr.decode("utf-8", errors="replace").splitlines():
        if line.strip():
            diagnostics.append(Diagnostic(line, stream="stderr"))

    if exit_code == 0:
        status = TransferStatus.SUCCEEDED
    elif exit_code == 24:
        status = TransferStatus.PARTIAL
    else:
        status = TransferStatus.FAILED
    if exit_code < 0:
        message = f"rsync was terminated by signal {-exit_code}."
    else:
        message = _EXIT_MESSAGES.get(exit_code, f"rsync exited with unknown status {exit_code}.")
    return TransferResult(
        status=status,
        exit_code=exit_code,
        message=message,
        changes=tuple(changes),
        diagnostics=tuple(diagnostics),
        last_progress=last_progress,
    )
