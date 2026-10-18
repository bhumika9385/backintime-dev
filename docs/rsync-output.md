# rsync output protocol

`backintime.core.rsync_output` parses rsync output into structured events. rsync must be
run with `OUTPUT_ARGS`:

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
