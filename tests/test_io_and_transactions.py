from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stateweave.cli import main
from stateweave.core.audit import audit_repository
from stateweave.core.errors import PathBoundaryError, RecordError
from stateweave.core.io import read_json, safe_relative_path, sha256_file
from stateweave.core.locking import inspect_writer_lock, recover_stale_writer_lock
from stateweave.core.project import put_record, put_records, recover_record_transaction
from stateweave.core.transactions import (
    load_transaction,
    transaction_id_for_key,
    write_transaction_journal,
)

from tests.helpers import fact, project, write_fact


class IoAndTransactionTests(unittest.TestCase):
    def test_non_finite_json_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "record.json"
            path.write_text('{"value": NaN}', encoding="utf-8")
            with self.assertRaises(RecordError):
                read_json(path, max_bytes=1024)

    def test_portable_relative_path_rejects_ambiguous_forms(self) -> None:
        for value in (
            "../escape",
            "..\\escape",
            "C:\\escape",
            "file:stream",
            "/absolute",
            "",
        ):
            with self.subTest(value=value):
                with self.assertRaises(PathBoundaryError):
                    safe_relative_path(value)
        self.assertEqual(
            safe_relative_path("memory/facts/FCT-one.json").as_posix(),
            "memory/facts/FCT-one.json",
        )

    def test_reciprocal_supersession_can_be_written_as_one_batch(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            write_fact(config, fact("FCT-old", value="old"))
            old = fact(
                "FCT-old",
                status="deprecated",
                superseded_by="FCT-new",
                value="old",
            )
            new = fact(
                "FCT-new",
                supersedes=["FCT-old"],
                value="new",
            )

            put_records(config, [old, new], overwrite=True)

            report = audit_repository(config)
            self.assertTrue(report.ok, report.errors)

    def test_invalid_batch_rolls_back_every_record(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            original = fact("FCT-old", value="old")
            path = write_fact(config, original)
            old = fact(
                "FCT-old",
                status="deprecated",
                superseded_by="FCT-missing",
                value="old",
            )
            with self.assertRaises(RecordError):
                put_records(config, [old], overwrite=True)
            self.assertEqual(
                read_json(path, max_bytes=config.limits.max_record_bytes),
                original,
            )

    def test_idempotent_replay_does_not_rewrite_a_committed_record(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            payload = fact("FCT-idempotent", value="stable")

            first = put_record(
                config,
                payload,
                idempotency_key="agent-session-001",
            )
            observed = first.read_bytes()
            replay = put_record(
                config,
                payload,
                idempotency_key="agent-session-001",
            )

            self.assertEqual(replay, first)
            self.assertEqual(replay.read_bytes(), observed)
            transaction_id, _ = transaction_id_for_key("agent-session-001")
            journal = load_transaction(config, transaction_id)
            self.assertEqual(journal.data["status"], "committed")
            self.assertEqual(len(list(config.facts_dir.glob("*.json"))), 1)

            changed = fact("FCT-idempotent", value="different")
            with self.assertRaisesRegex(RecordError, "different request"):
                put_record(
                    config,
                    changed,
                    overwrite=True,
                    idempotency_key="agent-session-001",
                )

    def test_optimistic_revision_precondition_rejects_lost_update(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            path = write_fact(config, fact("FCT-revision", value="one"))
            revision = sha256_file(path)
            updated = fact("FCT-revision", value="two")

            put_record(
                config,
                updated,
                overwrite=True,
                expected_sha256=revision,
            )
            committed = path.read_bytes()

            with self.assertRaisesRegex(RecordError, "precondition failed"):
                put_record(
                    config,
                    fact("FCT-revision", value="three"),
                    overwrite=True,
                    expected_sha256=revision,
                )
            self.assertEqual(path.read_bytes(), committed)

    def test_fact_and_current_state_update_share_one_transaction(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            state_revision = sha256_file(config.state_file)
            new_fact = fact("FCT-state-source", value="active")
            state = read_json(
                config.state_file,
                max_bytes=config.limits.max_record_bytes,
            )
            state["updated_at"] = "2026-07-27T12:00:00Z"
            state["references"] = ["FCT-state-source"]
            state["items"] = [
                {
                    "source_id": "FCT-state-source",
                    "summary": "Synthetic work remains active.",
                    "status": "active",
                }
            ]

            put_records(
                config,
                [new_fact, state],
                overwrite=True,
                expected_sha256_by_id={
                    "FCT-state-source": None,
                    "STATE-current": state_revision,
                },
                idempotency_key="fact-and-state",
            )

            self.assertTrue(audit_repository(config).ok)
            observed = read_json(
                config.state_file,
                max_bytes=config.limits.max_record_bytes,
            )
            self.assertEqual(observed["items"][0]["source_id"], "FCT-state-source")

    def test_incomplete_journal_blocks_audit_until_explicit_recovery(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            original = fact("FCT-interrupted", value="original")
            path = write_fact(config, original)
            invalid = fact(
                "FCT-interrupted",
                status="deprecated",
                superseded_by="FCT-missing",
                value="invalid",
            )
            calls = 0

            def fail_to_close(path: Path, payload: dict[str, object]) -> None:
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise OSError("injected journal close failure")
                write_transaction_journal(path, payload)

            with patch(
                "stateweave.core.project.write_transaction_journal",
                side_effect=fail_to_close,
            ):
                with self.assertRaisesRegex(RecordError, "could not close"):
                    put_record(
                        config,
                        invalid,
                        overwrite=True,
                        idempotency_key="interrupted-session",
                    )

            self.assertEqual(
                read_json(path, max_bytes=config.limits.max_record_bytes),
                original,
            )
            transaction_id, _ = transaction_id_for_key("interrupted-session")
            transaction = load_transaction(config, transaction_id)
            self.assertEqual(transaction.data["status"], "applying")
            self.assertTrue(transaction.path.is_file())
            blocked = audit_repository(config)
            self.assertFalse(blocked.ok)
            self.assertIn("requires explicit recovery", "; ".join(blocked.errors))

            recovered = recover_record_transaction(
                config,
                transaction_id,
                expected_request_sha256=transaction.data["request_sha256"],
            )

            self.assertEqual(recovered["status"], "rolled_back")
            self.assertEqual(recovered["error_type"], "RecoveredInterruptedTransaction")
            self.assertTrue(audit_repository(config).ok)
            self.assertFalse(transaction.payload_dir.exists())

    def test_cli_transaction_recovery_requires_fingerprint_and_confirmation(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            original = fact("FCT-cli-recovery", value="original")
            path = write_fact(config, original)
            changed = fact(
                "FCT-cli-recovery",
                status="deprecated",
                superseded_by="FCT-missing",
                value="changed",
            )
            calls = 0

            def fail_to_close(path: Path, payload: dict[str, object]) -> None:
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise OSError("injected journal close failure")
                write_transaction_journal(path, payload)

            with patch(
                "stateweave.core.project.write_transaction_journal",
                side_effect=fail_to_close,
            ):
                with self.assertRaises(RecordError):
                    put_record(
                        config,
                        changed,
                        overwrite=True,
                        idempotency_key="cli-recovery-session",
                    )

            transaction_id, _ = transaction_id_for_key("cli-recovery-session")
            status_output = StringIO()
            with redirect_stdout(status_output):
                status = main(["transaction-status", "--config", str(config.root)])
            self.assertEqual(status, 0)
            status_payload = json.loads(status_output.getvalue())
            observed = next(
                item
                for item in status_payload["transactions"]
                if item["id"] == transaction_id
            )

            error = StringIO()
            with redirect_stderr(error):
                refused = main(
                    [
                        "recover-transaction",
                        transaction_id,
                        "--config",
                        str(config.root),
                        "--request-sha256",
                        observed["request_sha256"],
                    ]
                )
            self.assertEqual(refused, 2)
            self.assertIn("--confirm-rollback", error.getvalue())

            recovery_output = StringIO()
            with redirect_stdout(recovery_output):
                recovered = main(
                    [
                        "recover-transaction",
                        transaction_id,
                        "--config",
                        str(config.root),
                        "--request-sha256",
                        observed["request_sha256"],
                        "--confirm-rollback",
                    ]
                )
            self.assertEqual(recovered, 0)
            self.assertEqual(
                json.loads(recovery_output.getvalue())["status"],
                "rolled_back",
            )
            self.assertEqual(
                read_json(path, max_bytes=config.limits.max_record_bytes),
                original,
            )

    def test_abrupt_process_exit_is_detected_and_recoverable(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            script = """
import os
import sys
from unittest.mock import patch

import stateweave.core.project as project_module
from stateweave.core.config import load_config
from tests.helpers import fact

config = load_config(sys.argv[1])
first = fact("FCT-crash-a", references=["FCT-crash-b"])
second = fact("FCT-crash-b", references=["FCT-crash-a"])
real_write = project_module.atomic_write_bytes

def crash_after_first_record(path, payload):
    real_write(path, payload)
    if path.parent == config.facts_dir:
        os._exit(86)

with patch.object(
    project_module,
    "atomic_write_bytes",
    side_effect=crash_after_first_record,
):
    project_module.put_records(
        config,
        [first, second],
        idempotency_key="abrupt-process-session",
    )
"""
            environment = dict(os.environ)
            source_root = str(Path(__file__).resolve().parents[1] / "src")
            environment["PYTHONPATH"] = os.pathsep.join(
                value
                for value in (source_root, environment.get("PYTHONPATH", ""))
                if value
            )
            crashed = subprocess.run(
                [sys.executable, "-c", script, str(config.root)],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(crashed.returncode, 86, crashed.stderr)
            self.assertTrue((config.facts_dir / "FCT-crash-a.json").exists())
            self.assertFalse((config.facts_dir / "FCT-crash-b.json").exists())
            self.assertFalse(audit_repository(config).ok)

            owner_path = config.metadata_dir / "writer.lock" / "owner.json"
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
            owner["acquired_at"] = "2000-01-01T00:00:00Z"
            owner_path.write_text(json.dumps(owner), encoding="utf-8")
            lock = inspect_writer_lock(
                config.metadata_dir,
                stale_after_seconds=config.limits.lock_stale_after_seconds,
            )
            assert lock.owner_sha256 is not None
            recover_stale_writer_lock(
                config.metadata_dir,
                stale_after_seconds=config.limits.lock_stale_after_seconds,
                expected_owner_sha256=lock.owner_sha256,
                expected_token=lock.owner_token,
            )

            transaction_id, _ = transaction_id_for_key("abrupt-process-session")
            transaction = load_transaction(config, transaction_id)
            self.assertEqual(transaction.data["status"], "applying")
            recover_record_transaction(
                config,
                transaction_id,
                expected_request_sha256=transaction.data["request_sha256"],
            )

            self.assertFalse((config.facts_dir / "FCT-crash-a.json").exists())
            self.assertFalse((config.facts_dir / "FCT-crash-b.json").exists())
            self.assertTrue(audit_repository(config).ok)


if __name__ == "__main__":
    unittest.main()
