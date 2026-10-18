"""Command-line entry point."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from backintime import __version__
from backintime.core.config import ConfigStore
from backintime.core.diagnostics import (
    DEFAULT_MIN_FREE_BYTES,
    HealthState,
    RepositoryReport,
    Severity,
    diagnose_repository,
)
from backintime.core.exceptions import BackInTimeError, ValidationError
from backintime.core.repository import Repository
from backintime.core.retention import RetentionPolicy, plan_retention

_RETENTION_RULES = ("keep_last", "keep_daily", "keep_weekly", "keep_monthly", "keep_yearly")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="backintime",
        description="Independent snapshot backup application.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        default=ConfigStore.default_path(),
        help="configuration file path",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("check-config", help="load and validate the configuration")
    diagnose = subparsers.add_parser(
        "diagnose",
        help="check backup repository health without modifying data",
    )
    diagnose.add_argument("--profile", help="only check the profile with this ID")
    diagnose.add_argument(
        "--min-free-mib",
        type=int,
        default=DEFAULT_MIN_FREE_BYTES // 1024**2,
        help="warn when the target has less free space than this (default: %(default)s)",
    )
    preview = subparsers.add_parser(
        "retention-preview",
        help="show which snapshots a retention policy would remove (dry run, deletes nothing)",
    )
    preview.add_argument("--profile", required=True, help="profile ID to preview")
    for rule in _RETENTION_RULES:
        preview.add_argument(
            f"--{rule.replace('_', '-')}",
            dest=rule,
            type=int,
            help=f"override the profile's {rule} rule",
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "check-config":
        try:
            configuration = ConfigStore(args.config).load()
        except BackInTimeError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1
        count = len(configuration.profiles)
        print(f"Configuration is valid ({count} profile{'s' if count != 1 else ''}).")
        return 0

    if args.command == "diagnose":
        return _diagnose(args.config, args.profile, args.min_free_mib)

    if args.command == "retention-preview":
        return _retention_preview(args)

    parser.print_help()
    return 0


def _diagnose(config_path: Path, profile_id: str | None, min_free_mib: int) -> int:
    """Print repository diagnostics; exit 2 when any problem is found."""
    try:
        configuration = ConfigStore(config_path).load()
    except BackInTimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    profiles = [
        profile
        for profile in configuration.profiles
        if profile_id is None or profile.profile_id == profile_id
    ]
    if profile_id is not None and not profiles:
        print(f"Profile not found: {profile_id}", file=sys.stderr)
        return 1
    if not profiles:
        print("No profiles are configured.")
        return 0

    problems = False
    for profile in profiles:
        report = diagnose_repository(profile.target, min_free_bytes=min_free_mib * 1024**2)
        _print_report(profile.profile_id, report)
        problems = (
            problems
            or report.state is not HealthState.HEALTHY
            or any(finding.severity is not Severity.INFO for finding in report.findings)
        )
    return 2 if problems else 0


def _print_report(profile_id: str, report: RepositoryReport) -> None:
    print(f"Profile '{profile_id}': {report.state.value}")
    print(f"  Repository: {report.repository}")
    if report.snapshots:
        print(f"  Snapshots: {report.healthy_snapshot_count} healthy of {len(report.snapshots)}")
    for finding in report.findings:
        print(f"  [{finding.severity.value}] {finding.subject}: {finding.message}")
        print(f"      Next step: {finding.guidance}")


def _retention_preview(args: argparse.Namespace) -> int:
    """Print a dry-run retention plan for one profile."""
    try:
        configuration = ConfigStore(args.config).load()
        profile = next(
            (item for item in configuration.profiles if item.profile_id == args.profile), None
        )
        if profile is None:
            print(f"Profile not found: {args.profile}", file=sys.stderr)
            return 1
        rules = profile.extensions.get("retention", {})
        overrides = {
            rule: getattr(args, rule)
            for rule in _RETENTION_RULES
            if getattr(args, rule) is not None
        }
        if not rules and not overrides:
            print(
                f"Profile '{profile.profile_id}' has no retention rules. Add a 'retention' "
                "object to the profile or pass --keep-* options.",
                file=sys.stderr,
            )
            return 1
        if not isinstance(rules, dict):
            raise ValidationError(
                f"Profile '{profile.profile_id}' 'retention' must be a JSON object."
            )
        policy = RetentionPolicy.from_mapping({**rules, **overrides})
        records = Repository(profile.target).list_snapshots(include_incomplete=True)
    except BackInTimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    plan = plan_retention(records, policy)
    print(f"Retention preview for profile '{profile.profile_id}' (dry run, nothing is deleted)")
    for entry in plan.entries:
        print(f"  {entry.action.value:<6} {entry.snapshot_id}  ({'; '.join(entry.reasons)})")
    print(
        f"{len(plan.keep)} kept, {len(plan.remove)} would be removed, "
        f"{len(plan.skip)} skipped as incomplete or invalid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
