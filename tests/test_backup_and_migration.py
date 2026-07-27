from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stateweave.core.audit import audit_repository
from stateweave.core.backup import (
    MANIFEST_NAME,
    create_backup,
    restore_backup,
)
from stateweave.core.config import load_config
from stateweave.core.errors import BackupError, MigrationError
from stateweave.core.io import canonical_json_bytes, sha256_bytes
from stateweave.core.migrations import apply_migration, plan_migration

from tests.helpers import project


def legacy_fact(identifier: str) -> dict[str, object]:
    return {
        "id": identifier,
        "title": "Synthetic legacy fact",
        "statement": "Synthetic only.",
        "status": "verified",
        "domain": "general",
        "verified_at": "2026-07-20T12:00:00Z",
        "review_after": "2026-08-15",
        "confidence": "high",
        "owner": "maintainer",
        "classification": "internal",
        "sources": [
            {
                "url": "https://example.invalid/source",
                "publisher": "Synthetic Publisher",
                "accessed_at": "2026-07-20",
                "kind": "primary",
            }
        ],
        "supersedes": [],
        "superseded_by": None,
    }


class BackupAndMigrationTests(unittest.TestCase):
    def test_backup_restore_round_trip_into_clean_destination(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            config = project(base / "source")
            payload = canonical_json_bytes(legacy_fact("FCT-legacy"))
            (config.facts_dir / "FCT-legacy.json").write_bytes(payload)

            backup = create_backup(config, label="round-trip")
            manifest = restore_backup(backup, base / "restored")
            restored = load_config(base / "restored")

            self.assertEqual(manifest["project_id"], config.project_id)
            self.assertEqual(
                (restored.facts_dir / "FCT-legacy.json").read_bytes(),
                payload,
            )
            self.assertTrue(restored.decisions_dir.is_dir())
            self.assertTrue(restored.metadata_dir.is_dir())
            self.assertTrue(restored.backups_dir.is_dir())
            self.assertTrue(restored.migrations_dir.is_dir())
            self.assertTrue(restored.extensions_dir.is_dir())

    def test_backup_restores_opaque_extension_artifacts(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            config = project(base / "source")
            artifact = config.extensions_dir / "synthetic" / "receipt.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(canonical_json_bytes({"synthetic": True}))

            backup = create_backup(config, label="extensions")
            restore_backup(backup, base / "restored")
            restored = load_config(base / "restored")

            restored_artifact = restored.extensions_dir / "synthetic" / "receipt.json"
            self.assertEqual(restored_artifact.read_bytes(), artifact.read_bytes())

    def test_migration_is_planned_applied_and_journaled(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "source")
            path = config.facts_dir / "FCT-legacy.json"
            path.write_bytes(canonical_json_bytes(legacy_fact("FCT-legacy")))
            plan = plan_migration(
                config,
                from_version="0.1",
                to_version="1.0",
            )
            self.assertEqual(len(plan.changes), 1)

            journal_path = apply_migration(
                config,
                plan,
                validate_after=lambda: (
                    audit_repository(
                        config,
                        allow_active_writer=True,
                    ).errors
                ),
            )

            migrated = json.loads(path.read_text())
            journal = json.loads(journal_path.read_text())
            self.assertEqual(migrated["schema_version"], "1.0")
            self.assertEqual(migrated["kind"], "fact")
            self.assertEqual(journal["status"], "complete")
            self.assertTrue((config.root / journal["backup"]).is_file())

    def test_failed_migration_restores_original_bytes(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "source")
            path = config.facts_dir / "FCT-legacy.json"
            original = canonical_json_bytes(legacy_fact("FCT-legacy"))
            path.write_bytes(original)
            plan = plan_migration(
                config,
                from_version="0.1",
                to_version="1.0",
            )

            with self.assertRaises(MigrationError):
                apply_migration(config, plan, fail_after=1)

            self.assertEqual(path.read_bytes(), original)
            journals = list(config.migrations_dir.glob("MIG-*.json"))
            self.assertEqual(len(journals), 1)
            self.assertEqual(
                json.loads(journals[0].read_text())["status"], "rolled_back"
            )

    def test_restore_rejects_path_traversal_archive(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "malicious.zip"
            payload = b"escape"
            manifest = {
                "schema_version": 1,
                "created_at": "2026-07-25T00:00:00Z",
                "project_id": "synthetic-one",
                "files": [
                    {
                        "path": "../escape.txt",
                        "size": len(payload),
                        "sha256": sha256_bytes(payload),
                    }
                ],
            }
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.txt", payload)
                archive.writestr(MANIFEST_NAME, canonical_json_bytes(manifest))

            with self.assertRaises(BackupError):
                restore_backup(archive_path, root / "restored")
            self.assertFalse((root / "escape.txt").exists())

    def test_restore_rejects_windows_ambiguous_member_names(self) -> None:
        for unsafe in ("..\\escape.txt", "C:\\escape.txt", "safe:stream"):
            with self.subTest(unsafe=unsafe), TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive_path = root / "malicious.zip"
                payload = b"escape"
                manifest = {
                    "schema_version": 1,
                    "created_at": "2026-07-25T00:00:00Z",
                    "project_id": "synthetic-one",
                    "files": [
                        {
                            "path": unsafe,
                            "size": len(payload),
                            "sha256": sha256_bytes(payload),
                        }
                    ],
                }
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(unsafe, payload)
                    archive.writestr(
                        MANIFEST_NAME,
                        canonical_json_bytes(manifest),
                    )
                with self.assertRaises(BackupError):
                    restore_backup(archive_path, root / "restored")

    def test_failed_restore_leaves_destination_empty(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = project(root / "source")
            (config.facts_dir / "FCT-legacy.json").write_bytes(
                canonical_json_bytes(legacy_fact("FCT-legacy"))
            )
            backup = create_backup(config)
            destination = root / "restored"
            destination.mkdir()
            with patch(
                "stateweave.core.backup.atomic_write_bytes",
                side_effect=OSError("injected write failure"),
            ):
                with self.assertRaises(OSError):
                    restore_backup(backup, destination)
            self.assertTrue(destination.is_dir())
            self.assertEqual(list(destination.iterdir()), [])
            self.assertFalse(list(root.glob(".restored.restore-*")))

    def test_restore_refuses_non_empty_destination(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = project(root / "source")
            backup = create_backup(config)
            destination = root / "owned"
            destination.mkdir()
            (destination / "preserve.txt").write_text("owned", encoding="utf-8")
            with self.assertRaises(BackupError):
                restore_backup(backup, destination)
            self.assertEqual(
                (destination / "preserve.txt").read_text(encoding="utf-8"),
                "owned",
            )

    def test_migration_rejects_unregistered_source_version(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "source")
            payload = legacy_fact("FCT-legacy")
            payload["schema_version"] = "9.9"
            (config.facts_dir / "FCT-legacy.json").write_bytes(
                canonical_json_bytes(payload)
            )
            with self.assertRaises(MigrationError):
                plan_migration(
                    config,
                    from_version="0.1",
                    to_version="1.0",
                )

    def test_backup_rejects_content_hidden_from_the_canonical_layout(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            nested = config.facts_dir / "nested"
            nested.mkdir()
            (nested / "FCT-hidden.json").write_text("{}", encoding="utf-8")

            with self.assertRaises(BackupError) as captured:
                create_backup(config)

            self.assertIn("unexpected directory", str(captured.exception))

    def test_migration_rejects_content_hidden_from_the_canonical_layout(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            nested = config.decisions_dir / "nested"
            nested.mkdir()
            (nested / "DEC-hidden.json").write_text("{}", encoding="utf-8")

            with self.assertRaises(MigrationError) as captured:
                plan_migration(config, from_version="0.1", to_version="1.0")

            self.assertIn("unexpected directory", str(captured.exception))


if __name__ == "__main__":
    unittest.main()
