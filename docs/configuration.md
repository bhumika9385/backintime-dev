# Configuration and profiles

BackInTime reads a versioned JSON configuration file. By default, its location is:

- `$XDG_CONFIG_HOME/backintime/config.json` when `XDG_CONFIG_HOME` is set.
- `~/.config/backintime/config.json` otherwise.

You can check a specific configuration without starting a backup:

```sh
python -m backintime.cli.main --config docs/examples/config.example.json check-config
```

The installed `backintime` command accepts the same arguments:

```sh
backintime --config /path/to/config.json check-config
```

`--config` is a global CLI option and therefore goes before `check-config`.

## Top-level fields

| Field | Required | Description |
| --- | --- | --- |
| `version` | Yes | Configuration schema version. The current supported version is `1`. |
| `profiles` | No | Array of profiles. If omitted, the configuration contains no profiles. |

An absent configuration file is treated as an empty configuration. A present but
unreadable, malformed, or unsupported-version file is an error; it is not silently
replaced with defaults.

## Profile fields

| Field | Required | Description |
| --- | --- | --- |
| `profile_id` | Yes | Stable identifier, 1–64 characters: lowercase ASCII letters, digits, `_`, and `-`; the first character must be a lowercase letter or digit. |
| `name` | Yes | Human-readable name; must contain a non-whitespace character. |
| `sources` | Yes | Non-empty array of absolute source path strings. |
| `target` | Yes | Absolute target path string. |
| `excludes` | No | Array of exclusion-rule strings. Defaults to an empty array. |
| `retention` | No | Retention rules used by `retention-preview`; see [Retention rules](#retention-rules). |

Profile IDs must be unique. A target may not equal a source or be inside a source
directory, which prevents configuring a backup destination that would recursively
include itself. Source and target paths must be absolute. Replace the example paths
with paths appropriate for your Linux system; the example paths are not created by
`check-config`.

The current profile model only validates exclusion strings as strings without a null
character. Rsync filter syntax and backup behavior are not implemented yet, so exclusions
are stored but are not currently applied to any backup.

Unknown top-level and per-profile fields are preserved when the configuration is loaded
and saved. They are extension points, not currently supported settings; do not rely on
an unknown option affecting application behavior.

## Retention rules

A profile can define a `retention` object. It is read by `backintime retention-preview`,
which only reports what would be removed. **No command deletes snapshots yet.**

```json
"retention": {"keep_last": 3, "keep_daily": 7, "keep_weekly": 4, "keep_monthly": 12, "keep_yearly": 2}
```

| Rule | Keeps |
| --- | --- |
| `keep_last` | The newest N snapshots. |
| `keep_daily` | The newest snapshot of each of the N most recent days that have one. |
| `keep_weekly` | The newest snapshot of each of the N most recent ISO weeks that have one. |
| `keep_monthly` | The newest snapshot of each of the N most recent months that have one. |
| `keep_yearly` | The newest snapshot of each of the N most recent years that have one. |

Every rule is optional and defaults to `0`, but at least one rule must be greater than
zero. Values must be integers from 0 to 100000, and unknown rule names are rejected. A
snapshot is kept when any rule keeps it. The following safety rules override the policy:

- Only snapshots whose `info.json` says `"state": "complete"` (with a matching
  `snapshot_id`) can be selected for removal. Incomplete or invalid snapshots are
  reported as `skip`.
- Snapshots whose metadata has `"protected": true` or a `"name"` string are always kept.
- The newest valid snapshot is always kept, so the last restorable backup cannot be
  selected for removal.

Snapshot times come from the snapshot ID and are compared as they are written, without
time-zone conversion.

```sh
backintime retention-preview --profile demo
backintime retention-preview --profile demo --keep-last 5 --keep-monthly 6
```

`--keep-*` options override the matching rules from the profile for that preview only.

## Example

[`examples/config.example.json`](examples/config.example.json) is a complete sample
using disposable Linux paths:

```json
{
  "version": 1,
  "profiles": [
    {
      "profile_id": "demo",
      "name": "Disposable demo files",
      "sources": [
        "/tmp/backintime-demo/source"
      ],
      "target": "/tmp/backintime-demo/snapshots",
      "excludes": [
        "*.cache",
        "temporary/"
      ]
    }
  ]
}
```

The source and target paths above are examples only. The application does not create
them when checking the configuration, and no backup command is currently implemented.
The `excludes` entries document the current stored shape only; no rsync filter behavior
is promised yet.

To validate the checked-in example from the repository root on Linux:

```sh
python -m backintime.cli.main --config docs/examples/config.example.json check-config
```

Expected output:

```text
Configuration is valid (1 profile).
```

## Persistence and safety

Configuration updates made through the core store are validated before writing and
replace the destination file atomically. On POSIX systems, the temporary configuration
file is created with owner-only permissions before replacement. Keep configuration
files private if they contain personal paths. Do not put passwords, private keys, or
other secrets in configuration; secret-management features are not implemented.

Profile editing through the API and GUI is not available yet. The API currently reads
configured profiles only. Backups, restores, rsync execution, exclusions, and target
availability checks are also not implemented, so a valid configuration does not mean a
profile is ready to perform a backup.
