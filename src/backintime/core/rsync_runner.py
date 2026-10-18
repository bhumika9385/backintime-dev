"""Run rsync and report its output as structured events.

This module is the execution layer for :mod:`backintime.core.rsync_output`.
It starts rsync with the parser's output protocol, streams stdout through the
parser while the transfer runs, and returns a :class:`TransferResult` whose
outcome is decided by rsync's exit status alone.

Backup orchestration, cancellation, and remote transports are out of scope
here; callers decide which source and destination arguments to pass.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from typing import IO

from backintime.core.exceptions import BackInTimeError, ValidationError
from backintime.core.rsync_output import (
    OUTPUT_ARGS,
    Diagnostic,
    Event,
    RsyncOutputParser,
    TransferResult,
    TransferStatus,
    summarize,
)

EventCallback = Callable[[Event], None]

_logger = logging.getLogger("backintime.rsync")
_READ_SIZE = 64 * 1024
_STOP_TIMEOUT = 5.0
# Options that would change or suppress the output protocol the parser relies on.
_RESERVED_LONG_OPTIONS = ("--out-format", "--log-format", "--info", "--progress", "--quiet")
_RESERVED_SHORT_OPTIONS = frozenset("qP")


class RsyncUnavailableError(BackInTimeError):
    """Raised when the rsync executable cannot be found or started."""


class RsyncTransferError(BackInTimeError):
    """Raised by :func:`check_result` when a transfer did not fully succeed.

    The complete :class:`TransferResult`, including every diagnostic line, is
    available as :attr:`result` for logging and status reporting.
    """

    def __init__(self, result: TransferResult) -> None:
        super().__init__(f"rsync transfer {result.status.value}: {result.message}")
        self.result = result


def build_command(arguments: Sequence[str], *, executable: str = "rsync") -> list[str]:
    """Return the rsync command line with the structured output protocol enabled.

    Arguments that would override or suppress the protocol are rejected, since
    they would make change and progress reporting unreliable.
    """
    for argument in arguments:
        if any(
            argument == option or argument.startswith(f"{option}=")
            for option in _RESERVED_LONG_OPTIONS
        ):
            raise ValidationError(f"rsync option {argument!r} is managed by BackInTime.")
        if (
            argument.startswith("-")
            and not argument.startswith("--")
            and _RESERVED_SHORT_OPTIONS.intersection(argument[1:])
        ):
            raise ValidationError(f"rsync option {argument!r} is managed by BackInTime.")
    return [executable, *OUTPUT_ARGS, *arguments]


def run_rsync(
    arguments: Sequence[str],
    *,
    on_event: EventCallback | None = None,
    executable: str | None = None,
) -> TransferResult:
    """Run rsync to completion and return its structured result.

    ``on_event`` is called from the calling thread for every parsed event as
    soon as it is read, so callers can publish live progress. Exceptions raised
    by the callback propagate after rsync has been stopped.
    """
    resolved = executable or shutil.which("rsync")
    if resolved is None:
        raise RsyncUnavailableError("rsync is not installed or not on PATH.")
    command = build_command(arguments, executable=resolved)

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Own process group, so rsync's forked helpers can be stopped together.
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise RsyncUnavailableError(f"rsync could not be started: {exc}") from exc

    assert process.stdout is not None and process.stderr is not None
    stderr_chunks: list[bytes] = []
    stderr_reader = threading.Thread(
        target=_drain, args=(process.stderr, stderr_chunks), daemon=True
    )
    stderr_reader.start()

    parser = RsyncOutputParser()
    events: list[Event] = []

    def publish(new_events: list[Event]) -> None:
        events.extend(new_events)
        if on_event is not None:
            for event in new_events:
                on_event(event)

    stopped = False
    try:
        stdout_fd = process.stdout.fileno()
        while chunk := os.read(stdout_fd, _READ_SIZE):
            publish(parser.feed(chunk))
        publish(parser.close())
    except BaseException:
        stopped = True
        _stop(process)
        raise
    finally:
        exit_code = process.wait()
        # After a forced stop, a helper may still hold stderr open; do not hang on it.
        stderr_reader.join(_STOP_TIMEOUT if stopped else None)
        process.stdout.close()
        if not stderr_reader.is_alive():
            process.stderr.close()

    return summarize(exit_code, events, b"".join(stderr_chunks))


def check_result(result: TransferResult) -> TransferResult:
    """Return ``result`` if it succeeded, otherwise raise :class:`RsyncTransferError`."""
    if not result.succeeded:
        raise RsyncTransferError(result)
    return result


def log_result(result: TransferResult, logger: logging.Logger = _logger) -> None:
    """Write a summary and every diagnostic line to the log for troubleshooting."""
    level = {
        TransferStatus.SUCCEEDED: logging.INFO,
        TransferStatus.PARTIAL: logging.WARNING,
        TransferStatus.FAILED: logging.ERROR,
    }[result.status]
    logger.log(
        level,
        "rsync %s (exit %d): %s %d change(s), %d diagnostic line(s).",
        result.status.value,
        result.exit_code,
        result.message,
        len(result.changes),
        len(result.diagnostics),
    )
    for diagnostic in result.diagnostics:
        logger.log(
            _diagnostic_level(diagnostic), "rsync %s: %s", diagnostic.stream, diagnostic.text
        )


def _diagnostic_level(diagnostic: Diagnostic) -> int:
    return logging.WARNING if diagnostic.stream == "stderr" else logging.INFO


def _stop(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _drain(stream: IO[bytes], chunks: list[bytes]) -> None:
    try:
        while chunk := stream.read(_READ_SIZE):
            chunks.append(chunk)
    except (OSError, ValueError):
        # The pipe was closed after rsync was stopped; nothing more to collect.
        pass
