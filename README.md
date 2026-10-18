
# BackInTime

**An independent backup and snapshot application focused on data integrity, safe restoration, and reliable recovery.**

> **Project status:** Early development. Features described as planned are not necessarily implemented or production-ready.

## Overview

BackInTime aims to provide reliable backups and snapshots while addressing common challenges in backup software, including deletion detection, snapshot integrity, restore safety, interrupted operations, and unclear error messages.

The project focuses on ensuring that backups accurately represent filesystem changes and that restore or snapshot-removal operations do not unexpectedly damage other data.

This repository is an independent implementation and is not affiliated with or endorsed by the original Back In Time project or its maintainers.

## Project Goals

- Create accurate snapshots of filesystem changes.
- Detect additions, modifications, and deletion-only changes.
- Prevent snapshot removal from corrupting other snapshots.
- Make restore operations predictable and safe.
- Handle concurrent backup and restore operations safely.
- Recover from interrupted operations where possible.
- Provide clear, actionable error messages.
- Test important edge cases using automated regression tests.

## Planned Features

- Local filesystem backups and timestamped snapshots.
- Efficient storage of unchanged files.
- Detection of file additions, modifications, and deletions.
- Selective file and directory restoration.
- Configurable include and exclude rules.
- Safe snapshot deletion and retention management.
- Backup and restore operation locking.
- Detection of incomplete or interrupted snapshots.
- Destination availability and permission checks.
- Clear logging and error reporting.
- Automated tests and continuous integration.
- A desktop interface and command-line workflow, subject to implementation.

## Reliability and Testing Priorities

The initial requirements are informed by backup reliability issues, including:

| Area | Expected Behaviour |
|---|---|
| Deletion-only backups | A backup must correctly record changes even when files were only deleted. |
| Snapshot integrity | Removing one snapshot must not unexpectedly change another snapshot's contents or metadata. |
| Include/exclude rules | Including a child path must not accidentally include unrelated contents of an excluded directory. |
| Restore safety | Restore operations must not delete unrelated or excluded live files unexpectedly. |
| Concurrent operations | Conflicting operations on the same backup profile must be coordinated safely. |
| Metadata preservation | Supported timestamps, permissions, and other metadata must be handled consistently. |
| Interrupted operations | Incomplete snapshots and interrupted removals must be detectable. |
| Error handling | External command failures must produce accurate and useful diagnostics. |
| Destination validation | Missing destinations and permission errors must be handled safely. |
| Regression testing | Every important bug fix should include a test that prevents the issue from returning. |

Issue numbers from other repositories may be mentioned in development notes as references, but they are not BackInTime issue identifiers unless created in this repository.

## Intended Technology

The initial technology direction is:

- **Language:** Python
- **API:** FastAPI with Pydantic validation
- **Web interface:** React, TypeScript, and Vite
- **Native desktop interface:** PySide6, planned
- **File transfer:** rsync where appropriate
- **State management:** SQLite or another reviewed approach
- **Testing:** pytest
- **Code quality:** Ruff
- **Development focus:** Linux-first

The architecture may evolve as implementation requirements are validated. Backup logic should remain independently testable and separate from GUI code wherever practical.

## Getting Started

The initial project foundation requires Python 3.10 or newer. From a checkout, install
the package and development tools in a virtual environment:

```sh
python -m pip install -e ".[dev]"
backintime --help
backintime --version
backintime check-config
python -m pytest
```

Configuration is versioned JSON stored at
`$XDG_CONFIG_HOME/backintime/config.json`, or `~/.config/backintime/config.json`
when `XDG_CONFIG_HOME` is not set. `check-config` accepts `--config PATH` to inspect a
specific file. No backup, restore, or snapshot deletion command is implemented yet.
See [the configuration guide](docs/configuration.md) for the current schema, validation
rules, and a disposable example. `backintime retention-preview --profile ID` shows which
snapshots a profile's [retention rules](docs/configuration.md#retention-rules) would
remove, without deleting anything.

### Repository health diagnostics

`backintime diagnose` checks the backup target of every configured profile (or one
profile with `--profile ID`) without creating, repairing, or deleting anything:

```sh
backintime diagnose
backintime diagnose --profile home --min-free-mib 2048
```

Each snapshot directory is reported as `healthy` (metadata says `complete`),
`incomplete` (no metadata or a state other than `complete`, usually an interrupted
backup), `invalid` (malformed metadata, a mismatched snapshot ID, or a link/file where a
directory is expected), or `inaccessible` (cannot be read). The repository takes the
worst snapshot state; a missing target, such as an unmounted drive, is `inaccessible`.
Low free space (below 1024 MiB by default) is reported as a warning. Every finding
includes a suggested next step. The command exits with `0` when everything is healthy,
`2` when it found a problem, and `1` when the configuration or profile cannot be loaded.

### Local API and frontend

Install backend and frontend dependencies in separate terminals:

```sh
python -m pip install -e ".[dev]"
python -m uvicorn backintime.api.app:app --host 127.0.0.1 --port 8000
```

```sh
cd frontend
npm ci
npm run dev
```

The Vite development server proxies `/api` to the loopback-only API. The API's
interactive OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.
The current API supports health checks, non-sensitive runtime capability checks, and
profile listing/detail and profile configuration CRUD. It does not start backups,
expose snapshots, or perform restore/prune operations. Profile `GET` responses include
an `ETag`; send that value as `If-Match` when creating, updating, or deleting profiles.
Stale revisions are rejected with HTTP 412. Profile deletion only removes configuration
and never deletes target files. Run a single API process so in-process update locking
and revision checks are shared by all requests. Do not bind the API to a network-facing
interface: the initial local application has no authentication. The API rejects
non-loopback `Host` values and untrusted browser `Origin` values; the default Vite
frontend origins (`http://127.0.0.1:5173`, `http://localhost:5173`, and
`http://[::1]:5173`) are allowed. Requests without an `Origin` are allowed for safe
methods so command-line clients can read status; state-changing methods must include an
allowed origin. CORS is not an authentication mechanism, and these checks do not make
network exposure safe.

#### Frontend API configuration

The frontend reads one optional build-time environment variable,
`VITE_API_BASE_URL`, in `frontend/src/lib/api.ts`. It sets the URL prefix for every
API request, and a trailing slash is removed.

- **Default (unset):** requests go to the same-origin path `/api/v1`. During
  `npm run dev`, the Vite server in `frontend/vite.config.ts` forwards every `/api`
  request to `http://127.0.0.1:8000` with `changeOrigin: false`, so the API sees a
  loopback `Host` header and the browser never makes a cross-origin request. This is
  the recommended setup for local development.
- **Custom value:** the frontend calls `${VITE_API_BASE_URL}/health`,
  `${VITE_API_BASE_URL}/system/info`, and `${VITE_API_BASE_URL}/profiles` directly
  instead of `/api/v1/...`. The value must include the API's `/api/v1` prefix. A
  same-origin path such as `/api/v1` works with the Vite proxy. An absolute URL on
  another origin, such as `http://127.0.0.1:8000/api/v1`, is a cross-origin request
  from the browser; the API does not currently send CORS headers, so browsers will
  block those responses. Keep the value on the same origin as the frontend, or use a
  same-origin proxy that forwards to the loopback API.

Vite inlines `VITE_*` variables into the JavaScript bundle at build time, so changing
the value requires restarting `npm run dev` or rebuilding. A local example, kept in
`frontend/.env.local` (Git ignores `.env.local` and `.env.*.local` files):

```sh
# frontend/.env.local
VITE_API_BASE_URL=/api/v1
```

Or for a single run:

```sh
cd frontend
VITE_API_BASE_URL=/api/v1 npm run dev
```

**Do not put passwords, API tokens, private keys, or other secrets in `VITE_*`
variables.** Every `VITE_*` value is embedded in the built JavaScript and can be read
by anyone who loads the frontend. The local API has no authentication, so no
credential is needed here.

Run frontend checks from `frontend/` with `npm test` and `npm run build`. The API and
frontend have been exercised in the development environment; Linux and rsync-specific
backup behaviour has not.

Use temporary directories and disposable test data. Do not test experimental backup or
restore functionality against your only copy of important data.

## Current Implementation Status

The initial foundation provides profile and configuration validation, atomic
configuration writes, strict snapshot-ID parsing, read-only listing of snapshots marked
complete in metadata, read-only repository health diagnostics, path-boundary
validation, basic CLI version/help/config checks,
and a local FastAPI/React interface for service status and configured profiles. The
backup engine, restore, deletion, retention, scheduling, and snapshot UI are not
implemented. This milestone has not been validated on Linux or with rsync.

## Testing Strategy

Tests should cover:

- New, modified, renamed, and deleted files.
- Backups where deletion is the only change.
- Empty files and nested directories.
- Filenames containing spaces or unusual characters.
- Include/exclude rule combinations.
- Single-file, directory, and full-snapshot restoration.
- Preservation of supported metadata.
- Missing destinations and insufficient permissions.
- Failed or interrupted external commands.
- Concurrent operations on the same profile.
- Snapshot removal and retention safety.
- Detection and recovery of incomplete snapshots.
- Clear reporting of external command exit codes.

Destructive tests must use isolated test directories and must never target real user data.

## Data Safety and Security

- Validate paths used by backup and restore operations.
- Prevent operations from unintentionally affecting files outside their intended scope.
- Never silently treat a failed or partial backup as successful.
- Make destructive actions explicit and testable.
- Avoid logging passwords, tokens, private keys, or unnecessary personal information.
- Report vulnerabilities privately according to [SECURITY.md](SECURITY.md).

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Do not publish security vulnerabilities in public issues. Follow the instructions in [SECURITY.md](SECURITY.md).

## Licence

This project uses the MIT License. See [LICENSE](LICENSE) for the complete licence text.

## Independence and Acknowledgements

This repository is intended to be an independent implementation. Existing backup applications and publicly documented issue reports may be used to understand common reliability problems.

The project does not claim affiliation with the original Back In Time project. Any third-party code, documentation, assets, or dependencies must be used in accordance with their applicable licences.

## Roadmap

1. Document architecture and data-safety requirements.
2. Implement a minimal local snapshot engine.
3. Add reliable detection of additions, modifications, and deletions.
4. Implement safe restoration.
5. Add snapshot integrity checks and safe removal.
6. Implement locking and interrupted-operation handling.
7. Add robust error reporting and regression tests.
8. Implement retention, scheduling, and a desktop interface.
9. Document verified installation steps and supported platforms.

---

**Warning:** This project is experimental. Do not rely on it as the sole protection for important data.
