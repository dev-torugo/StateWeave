from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stateweave.adoption import (
    apply_project_adoption,
    audit_adoption,
    discover_project_config,
    plan_project_adoption,
)
from stateweave.cli import main
from stateweave.core.backup import create_backup, restore_backup
from stateweave.core.config import load_config
from stateweave.core.errors import ConfigurationError, RecordError


class ExistingProjectAdoptionTests(unittest.TestCase):
    def _existing_project(self, root: Path) -> dict[str, bytes]:
        files = {
            "README.md": b"# Synthetic existing project\n",
            "src/app.py": b"print('synthetic')\n",
            "notes/design.txt": b"preserve exactly\n",
        }
        for relative, payload in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        return files

    def test_dry_run_is_read_only_and_reports_one_sidecar_write(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = self._existing_project(root)

            plan = plan_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
            )

            self.assertEqual(plan["status"], "safe")
            self.assertEqual(plan["deployment_mode"], "sidecar")
            self.assertEqual(plan["planned_writes"], [".stateweave-project"])
            self.assertEqual(plan["preserved_entry_count"], 3)
            self.assertFalse((root / ".stateweave-project").exists())
            for relative, payload in expected.items():
                self.assertEqual((root / relative).read_bytes(), payload)

    def test_apply_preserves_existing_bytes_and_cli_discovers_sidecar(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = self._existing_project(root)
            plan = plan_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
            )

            result = apply_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
                expected_plan_sha256=plan["plan_sha256"],
                adopted_at="2026-07-27T19:00:00Z",
                confirmed=True,
            )

            self.assertEqual(result["status"], "adopted")
            self.assertEqual(
                result["config_path"],
                ".stateweave-project/stateweave.toml",
            )
            self.assertEqual(result["receipt"]["preserved_entry_count"], 3)
            for relative, payload in expected.items():
                self.assertEqual((root / relative).read_bytes(), payload)
            config = load_config(discover_project_config(root))
            self.assertEqual(config.project_id, "existing-one")
            output = StringIO()
            with redirect_stdout(output):
                status = main(["audit", "--config", str(root), "--json"])
            self.assertEqual(status, 0)
            self.assertTrue(json.loads(output.getvalue())["ok"])
            output = StringIO()
            with redirect_stdout(output):
                adoption_status = main(["audit-adoption", "--config", str(root)])
            self.assertEqual(adoption_status, 0)
            self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_apply_refuses_plan_drift_without_writing_sidecar(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._existing_project(root)
            plan = plan_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
            )
            (root / "late-change.txt").write_text("drift", encoding="utf-8")

            with self.assertRaises(RecordError) as captured:
                apply_project_adoption(
                    root,
                    project_id="existing-one",
                    project_name="Existing One",
                    expected_plan_sha256=plan["plan_sha256"],
                    adopted_at="2026-07-27T19:00:00Z",
                    confirmed=True,
                )

            self.assertIn("plan changed", str(captured.exception))
            self.assertFalse((root / ".stateweave-project").exists())

    def test_failed_materialization_removes_only_owned_temporary_paths(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = self._existing_project(root)
            plan = plan_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
            )

            def fail_after_partial_write(
                destination: str | Path,
                **_: object,
            ) -> None:
                staging = Path(destination)
                staging.mkdir()
                (staging / "partial.txt").write_text("partial", encoding="utf-8")
                raise RecordError("synthetic adoption failure")

            with patch(
                "stateweave.adoption.project.initialize_project",
                side_effect=fail_after_partial_write,
            ):
                with self.assertRaises(RecordError):
                    apply_project_adoption(
                        root,
                        project_id="existing-one",
                        project_name="Existing One",
                        expected_plan_sha256=plan["plan_sha256"],
                        adopted_at="2026-07-27T19:00:00Z",
                        confirmed=True,
                    )

            self.assertFalse((root / ".stateweave-project").exists())
            self.assertFalse((root / ".stateweave-adoption.lock").exists())
            self.assertFalse(list(root.glob(".stateweave-adopt-*")))
            for relative, payload in expected.items():
                self.assertEqual((root / relative).read_bytes(), payload)

    def test_invalid_or_interrupted_sidecar_blocks_without_overwrite(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._existing_project(root)
            sidecar = root / ".stateweave-project"
            sidecar.mkdir()
            sentinel = sidecar / "owned.txt"
            sentinel.write_text("preserve", encoding="utf-8")

            plan = plan_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
            )

            self.assertEqual(plan["status"], "blocked")
            self.assertTrue(plan["conflicts"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_repeated_adoption_reports_already_adopted_without_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._existing_project(root)
            first = plan_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
            )
            applied = apply_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
                expected_plan_sha256=first["plan_sha256"],
                adopted_at="2026-07-27T19:00:00Z",
                confirmed=True,
            )
            receipt_path = next(
                (
                    root
                    / ".stateweave-project"
                    / ".stateweave"
                    / "extensions"
                    / "adoption"
                ).glob("ADP-*.json")
            )
            before = receipt_path.read_bytes()
            repeated = plan_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
            )

            self.assertEqual(repeated["status"], "already_adopted")
            replay = apply_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
                expected_plan_sha256=repeated["plan_sha256"],
                adopted_at="2026-07-27T20:00:00Z",
                confirmed=True,
            )
            self.assertEqual(replay["status"], "already_adopted")
            self.assertEqual(replay["receipt"], applied["receipt"])
            self.assertEqual(receipt_path.read_bytes(), before)

    def test_cli_requires_hash_timestamp_and_confirmation_to_apply(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._existing_project(root)
            output = StringIO()
            with redirect_stdout(output):
                dry_status = main(
                    [
                        "adopt",
                        str(root),
                        "--id",
                        "existing-one",
                        "--name",
                        "Existing One",
                    ]
                )
            self.assertEqual(dry_status, 0)
            plan = json.loads(output.getvalue())

            error = StringIO()
            with redirect_stderr(error):
                refused = main(
                    [
                        "adopt",
                        str(root),
                        "--id",
                        "existing-one",
                        "--name",
                        "Existing One",
                        "--apply",
                        "--expected-plan-sha256",
                        plan["plan_sha256"],
                        "--adopted-at",
                        "2026-07-27T19:00:00Z",
                    ]
                )
            self.assertEqual(refused, 2)
            self.assertIn("explicit confirmation", error.getvalue())
            self.assertFalse((root / ".stateweave-project").exists())

    def test_config_discovery_rejects_sidecar_symlink(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "existing"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            try:
                (root / ".stateweave-project").symlink_to(
                    outside,
                    target_is_directory=True,
                )
            except OSError:
                self.skipTest("directory symlinks are unavailable")

            with self.assertRaises(ConfigurationError):
                discover_project_config(root)

    def test_backup_restore_preserves_valid_adoption_receipt(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "existing"
            root.mkdir()
            self._existing_project(root)
            plan = plan_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
            )
            apply_project_adoption(
                root,
                project_id="existing-one",
                project_name="Existing One",
                expected_plan_sha256=plan["plan_sha256"],
                adopted_at="2026-07-27T19:00:00Z",
                confirmed=True,
            )
            config = load_config(discover_project_config(root))
            backup = create_backup(config, label="adoption")
            restore_backup(backup, base / "restored")
            restored = load_config(base / "restored")

            report = audit_adoption(restored)
            self.assertTrue(report.ok)
            self.assertEqual(report.receipt_count, 1)


if __name__ == "__main__":
    unittest.main()
