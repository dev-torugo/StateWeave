"""StateWeave command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from stateweave.core.audit import audit_repository
from stateweave.core.backup import create_backup, restore_backup
from stateweave.core.config import load_config
from stateweave.core.errors import StateWeaveError
from stateweave.core.migrations import apply_migration, plan_migration
from stateweave.core.project import initialize_project


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _today(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise StateWeaveError("--today must be an ISO date") from exc


def _config(value: str) -> Any:
    return load_config(Path(value))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stateweave",
        description="Persistent memory and governed workflow framework",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init", help="initialize an empty memory project")
    init.add_argument("destination")
    init.add_argument("--id", required=True, dest="project_id")
    init.add_argument("--name", required=True, dest="project_name")

    audit = subcommands.add_parser("audit", help="validate the memory graph")
    audit.add_argument("--config", default=".")
    audit.add_argument("--today")
    audit.add_argument("--json", action="store_true", dest="as_json")

    review = subcommands.add_parser(
        "review", help="show the deterministic review queue"
    )
    review.add_argument("--config", default=".")
    review.add_argument("--today")

    backlinks = subcommands.add_parser(
        "backlinks",
        help="show records that point to an identifier",
    )
    backlinks.add_argument("identifier")
    backlinks.add_argument("--config", default=".")

    backup = subcommands.add_parser("backup", help="create a verified backup")
    backup.add_argument("--config", default=".")
    backup.add_argument("--label", default="manual")

    restore = subcommands.add_parser(
        "restore",
        help="restore a verified backup into an empty destination",
    )
    restore.add_argument("backup")
    restore.add_argument("destination")

    migrate = subcommands.add_parser("migrate", help="plan or apply a migration")
    migrate.add_argument("--config", default=".")
    migrate.add_argument("--from-version", required=True)
    migrate.add_argument("--to-version", required=True)
    migrate.add_argument("--apply", action="store_true")
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "init":
        config = initialize_project(
            args.destination,
            project_id=args.project_id,
            project_name=args.project_name,
        )
        print(config.root)
        return 0
    if args.command == "restore":
        manifest = restore_backup(args.backup, args.destination)
        print(_json(manifest))
        return 0

    config = _config(args.config)
    if args.command in {"audit", "review", "backlinks"}:
        report = audit_repository(config, today=_today(getattr(args, "today", None)))
        if args.command == "audit":
            if args.as_json:
                print(_json(report.as_dict()))
            else:
                outcome = "OK" if report.ok else "FAILED"
                print(
                    f"Memory audit: {outcome} "
                    f"({report.record_count} records, "
                    f"{len(report.errors)} errors, "
                    f"{len(report.review_queue)} review items)"
                )
                for error in report.errors:
                    print(f"ERROR: {error}")
                for warning in report.warnings:
                    print(f"WARNING: {warning}")
            return 0 if report.ok else 1
        if args.command == "review":
            print(_json(report.as_dict()["review_queue"]))
            return 0 if report.ok else 1
        links = report.as_dict()["backlinks"].get(args.identifier, [])
        print(_json(links))
        return 0 if report.ok else 1
    if args.command == "backup":
        destination = create_backup(config, label=args.label)
        print(destination)
        return 0
    if args.command == "migrate":
        plan = plan_migration(
            config,
            from_version=args.from_version,
            to_version=args.to_version,
        )
        if not args.apply:
            print(_json(plan.as_dict(config.root)))
            return 0

        def validate() -> list[str]:
            return audit_repository(config, allow_active_writer=True).errors

        journal = apply_migration(config, plan, validate_after=validate)
        print(journal)
        return 0
    raise StateWeaveError(f"unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except StateWeaveError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
