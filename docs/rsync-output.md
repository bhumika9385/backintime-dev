# rsync output protocol

`backintime.core.rsync_output` parses rsync output into structured events, and
`backintime.core.rsync_runner` runs rsync with the required options. rsync must be run with
`OUTPUT_ARGS`:

```text
--out-format=BIT1 %i %n
--info=progress2
```

## Records

rsync stdout is split on both `\n` and `\r`. Each non-blank record is one of:

| Record | Example | Parsed as |
| --- | --- | --- |
| Change | `BIT1 >f+++++++++ docs/report.txt` | `ItemizedChange` |
| Deletion | `BIT1 *deleting   old.txt` | `ItemizedChange` with kind `deleted` |
| Progress | `4,096  45%  3.91kB/s  0:00:00 (xfr#5, to-chk=0/8)` | `Progress` |
| Anything else | `sent 1,234 bytes ...`, warnings, malformed records | `Diagnostic` (verbatim) |

A change record is `BIT1 `, the 11-character itemize string (`%i`, see rsync(1)), one
space, and the name. The itemize string has a fixed width, so names are never split on
whitespace. `%L` is deliberately not used, because ` -> ` can also appear in a name.

## File names

rsync escapes control characters such as newline (`\#012`) and carriage return
(`\#015`), and a backslash before `#` (`\#134`), as `\#ooo` octal sequences. Without
`-8`, it also escapes bytes that are not printable in the current locale. Tabs are **not**
escaped, which is why records are split only on `\n` and `\r`.

`decode_path` reverses the escaping and decodes the bytes as UTF-8 with
`surrogateescape`, so names that are not valid UTF-8 round-trip exactly. The undecoded
bytes are also kept as `ItemizedChange.raw_path`.

## Progress

`--info=progress2` reports whole-transfer progress. A percentage is only reported when
rsync shows a `to-chk=remaining/total` counter, meaning the file list is complete. While
rsync is still scanning (`ir-chk=...` or no counter), `Progress.percent` is `None` and
progress is indeterminate. Percentages above 100 are discarded. The parser never invents a
percentage.

## Outcome

`summarize(exit_code, events, stderr)` builds a `TransferResult`. The outcome comes only
from rsync's exit status: `0` succeeded, `24` (source files vanished) is partial, and
every other status (including termination by a signal) failed. Malformed output never
changes the outcome. All unrecognised stdout lines and every non-blank stderr line are
kept as `Diagnostic` entries for logs.

## Running rsync

`run_rsync(arguments, on_event=callback)` starts rsync with `OUTPUT_ARGS` followed by the
caller's arguments:

- **Live events:** stdout is parsed while rsync runs, and each change, progress, or
  diagnostic event is passed to `on_event` as soon as it is read, so a job can publish
  live progress.
- **No deadlock:** stderr is drained on a separate thread, so large error output cannot
  block the transfer.
- **Protected options:** `build_command` rejects options that would change or suppress
  the protocol (`--out-format`, `--log-format`, `--info`, `--progress`, `--quiet`, `-q`,
  `-P`) with a `ValidationError`.
- **Missing rsync:** if rsync is not installed or cannot be started,
  `RsyncUnavailableError` is raised.
- **Callback errors:** if the callback raises, rsync is stopped and the exception
  propagates. On POSIX, rsync runs in its own process group, so its forked helper
  processes are stopped too.
- **Checking the outcome:** `check_result(result)` raises `RsyncTransferError` for a
  partial or failed transfer. The full `TransferResult` is available as `error.result`.
- **Logging:** `log_result(result)` writes a summary and every diagnostic line to the
  `backintime.rsync` logger: the summary at INFO, WARNING, or ERROR by outcome, stderr
  lines at WARNING, and other stdout lines at INFO.

Cancellation, backup orchestration, and remote transports are not part of this layer.

## Status model

`backintime.api.schemas.TransferStatusResponse.from_result(result)` converts a result into
the API status shape:

```json
{
  "status": "succeeded",
  "succeeded": true,
  "exit_code": 0,
  "message": "Success.",
  "progress": {"indeterminate": false, "percent": 100, "bytes_transferred": 4096,
               "files_remaining": 0, "files_total": 3},
  "changes": {"total": 3, "by_kind": {"received": 1, "created": 1, "deleted": 1, "...": 0}},
  "diagnostic_count": 0
}
```

`TransferProgressResponse.from_progress(progress)` gives the same `progress` object for
live updates. When no reliable percentage exists it reports `"indeterminate": true` and
`"percent": null`. Diagnostic text can contain file paths, so the status model exposes
only `diagnostic_count`; the complete output goes to the log through `log_result`.
