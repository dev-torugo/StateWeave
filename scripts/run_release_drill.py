#!/usr/bin/env python3
"""Exercise migration, backup, restore, and post-restore audit end to end."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.core.audit import audit_repository
from stateweave.core.backup import create_backup, restore_backup
from stateweave.core.config import load_config
from stateweave.core.errors import StateWeaveError
from stateweave.core.io import atomic_write_json
from stateweave.core.migrations import apply_migration, plan_migration
from stateweave.core.project import initialize_project


def _legacy_fact() -> dict[str, object]:
    return {
        "id": "FCT-release-drill",
        "title": "Synthetic legacy release drill",
        "statement": "Synthetic fact used only for a local release drill.",
        "status": "verified",
        "domain": "general",
        "verified_at": "2026-07-20T12:00:00Z",
        "review_after": "2026-08-15",
        "confidence": "high",
        "owner": "maintainer",
        "classification": "internal",
        "sources": [
            {
                "url": "https://example.invalid/release-drill",
                "publisher": "Synthetic Publisher",
                "accessed_at": "2026-07-20",
                "kind": "primary",
            }
        ],
        "supersedes": [],
        "superseded_by": None,
    }


def run_drill() -> dict[str, object]:
    with TemporaryDirectory(prefix="stateweave-release-drill-") as temporary:
        root = Path(temporary)
        config = initialize_project(
            root / "source",
            project_id="release-drill",
            project_name="Synthetic Release Drill",
        )
        atomic_write_json(
            config.facts_dir / "FCT-release-drill.json",
            _legacy_fact(),
        )
        plan = plan_migration(
            config,
            from_version="0.1",
            to_version="1.0",
        )

        def validate_after() -> list[str]:
            return audit_repository(
                config,
                allow_active_writer=True,
            ).errors

        journal_path = apply_migration(
            config,
            plan,
            validate_after=validate_after,
        )
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        backup = create_backup(config, label="post-migration")
        restore_backup(backup, root / "restored")
        restored = load_config(root / "restored")
        report = audit_repository(restored)
        if not report.ok:
            raise StateWeaveError(
                "restored project failed audit: " + "; ".join(report.errors)
            )
        return {
            "migration_changes": len(plan.changes),
            "migration_status": journal["status"],
            "backup_created": backup.is_file(),
            "restored_records": report.record_count,
            "restored_ok": report.ok,
        }


def main() -> int:
    print(json.dumps(run_drill(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
