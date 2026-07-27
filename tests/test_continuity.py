from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.context import compile_context
from stateweave.cli import main
from stateweave.continuity import (
    append_orchestration_documents,
    append_workflow_documents,
    apply_mutation_plan,
    audit_continuity,
    capture_candidate,
    preview_candidate,
    promote_candidate,
    store_context_bundle,
    store_mutation_plan,
)
from stateweave.core.audit import audit_repository
from stateweave.core.backup import create_backup, restore_backup
from stateweave.core.config import load_config
from stateweave.core.errors import ContractError, RecordError
from stateweave.core.io import (
    canonical_json_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
)
from stateweave.core.project import put_record

from tests.helpers import decision, fact, project
from tests.test_context import memory_query
from tests.test_orchestration import orchestration_documents
from tests.test_workflow_and_policy import workflow_records


def synthetic_provenance(*derivation_ids: str) -> dict[str, object]:
    return {
        "repository_revision": "synthetic-revision",
        "tree_sha256": None,
        "artifact_path": "synthetic/source.py",
        "artifact_sha256": "2" * 64,
        "selector": "synthetic_symbol",
        "as_of": "2026-07-27T12:00:00Z",
        "extraction_method": "synthetic-parser",
        "observer": "local-agent",
        "derivation_ids": list(derivation_ids),
    }


def synthetic_source() -> dict[str, str]:
    return {
        "type": "filesystem",
        "locator": "synthetic/source.py",
        "observed_at": "2026-07-27T12:00:00Z",
    }


class ContinuityTests(unittest.TestCase):
    def test_candidate_update_preview_and_promotion_use_expected_revision(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            original = fact("FCT-candidate-update", value="before")
            original_path = put_record(config, original)
            revision = sha256_file(original_path)
            proposed = fact("FCT-candidate-update", value="after")
            proposed["statement"] = "Synthetic updated candidate is ready."
            candidate = capture_candidate(
                config,
                idempotency_key="candidate-update-001",
                captured_at="2026-07-27T12:00:00Z",
                classification="internal",
                confidence="high",
                source=synthetic_source(),
                provenance=synthetic_provenance(),
                proposed_record=proposed,
                operation="update",
                expected_sha256=revision,
            )

            preview = preview_candidate(config, candidate["id"])
            promoted = promote_candidate(
                config,
                candidate["id"],
                reviewer_role="maintainer",
                promoted_at="2026-07-27T12:01:00Z",
                human_approved=True,
            )

            self.assertEqual(preview["operation"], "update")
            self.assertEqual(preview["current_sha256"], revision)
            self.assertIn("claim", preview["changed_fields"])
            self.assertIn("statement", preview["changed_fields"])
            self.assertEqual(promoted["status"], "promoted")
            self.assertEqual(
                read_json(
                    original_path,
                    max_bytes=config.limits.max_record_bytes,
                )["claim"]["object"],
                "after",
            )

    def test_partial_episode_replay_does_not_duplicate_document_ids(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            source_decision = decision("DEC-episode-context")
            source_decision["decision"] = (
                "Use a synthetic episode replay context for validation."
            )
            put_record(config, source_decision)
            bundle = compile_context(
                config,
                memory_query(
                    "episode replay context",
                    terms=["episode", "replay"],
                    record_kinds=["decision"],
                    statuses=["accepted"],
                ),
            )
            store_context_bundle(config, bundle)
            documents = orchestration_documents()
            documents[5]["context_sha256"] = bundle["context_sha256"]

            first = append_orchestration_documents(config, documents)
            replay = append_orchestration_documents(config, [documents[0]])
            report = audit_continuity(config)

            self.assertEqual(replay, first)
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(report.episode_count, 1)

    def test_malformed_continuity_artifact_is_reported_without_crashing(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            candidates = config.extensions_dir / "continuity" / "candidates"
            candidates.mkdir(parents=True)
            malformed = candidates / f"CND-{'0' * 64}.json"
            malformed.write_text("{not-json", encoding="utf-8")

            report = audit_continuity(config)

            self.assertFalse(report.ok)
            self.assertIn("invalid JSON record", "; ".join(report.errors))

    def test_remember_and_promote_cli_keep_the_human_gate(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = project(root / "memory")
            proposed_path = root / "proposed.json"
            proposed_path.write_bytes(
                canonical_json_bytes(fact("FCT-cli-candidate", value="cli"))
            )
            remember_output = StringIO()
            with redirect_stdout(remember_output):
                captured = main(
                    [
                        "remember",
                        str(proposed_path),
                        "--config",
                        str(config.root),
                        "--idempotency-key",
                        "cli-candidate-001",
                        "--captured-at",
                        "2026-07-27T12:00:00Z",
                        "--classification",
                        "internal",
                        "--confidence",
                        "high",
                        "--source-type",
                        "filesystem",
                        "--source-locator",
                        "synthetic/source.py",
                        "--observed-at",
                        "2026-07-27T12:00:00Z",
                        "--artifact-path",
                        "synthetic/source.py",
                        "--artifact-sha256",
                        "2" * 64,
                        "--as-of",
                        "2026-07-27T12:00:00Z",
                        "--extraction-method",
                        "manual-entry",
                        "--observer",
                        "stateweave-cli",
                    ]
                )
            self.assertEqual(captured, 0)
            candidate = json.loads(remember_output.getvalue())

            preview_output = StringIO()
            with redirect_stdout(preview_output):
                preview_status = main(
                    [
                        "candidate-preview",
                        candidate["id"],
                        "--config",
                        str(config.root),
                    ]
                )
            self.assertEqual(preview_status, 0)
            self.assertEqual(
                json.loads(preview_output.getvalue())["operation"],
                "create",
            )

            error = StringIO()
            with redirect_stderr(error):
                refused = main(
                    [
                        "promote-candidate",
                        candidate["id"],
                        "--config",
                        str(config.root),
                        "--reviewer-role",
                        "maintainer",
                        "--promoted-at",
                        "2026-07-27T12:01:00Z",
                    ]
                )
            self.assertEqual(refused, 2)
            self.assertIn("human approval", error.getvalue())

            promote_output = StringIO()
            with redirect_stdout(promote_output):
                promoted = main(
                    [
                        "promote-candidate",
                        candidate["id"],
                        "--config",
                        str(config.root),
                        "--reviewer-role",
                        "maintainer",
                        "--promoted-at",
                        "2026-07-27T12:01:00Z",
                        "--confirm-human",
                    ]
                )
            self.assertEqual(promoted, 0)
            self.assertEqual(
                json.loads(promote_output.getvalue())["status"],
                "promoted",
            )

    def test_candidate_capture_is_idempotent_and_promotion_is_governed(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            proposed = fact("FCT-promoted-candidate", value="promoted")

            first = capture_candidate(
                config,
                idempotency_key="candidate-session-001",
                captured_at="2026-07-27T12:01:00Z",
                classification="internal",
                confidence="high",
                source=synthetic_source(),
                provenance=synthetic_provenance(),
                proposed_record=proposed,
            )
            replay = capture_candidate(
                config,
                idempotency_key="candidate-session-001",
                captured_at="2026-07-27T12:01:00Z",
                classification="internal",
                confidence="high",
                source=synthetic_source(),
                provenance=synthetic_provenance(),
                proposed_record=proposed,
            )

            self.assertEqual(first, replay)
            self.assertEqual(first["status"], "pending")
            self.assertNotIn("candidate-session-001", str(first))
            changed = fact("FCT-different-candidate", value="different")
            with self.assertRaisesRegex(RecordError, "different request"):
                capture_candidate(
                    config,
                    idempotency_key="candidate-session-001",
                    captured_at="2026-07-27T12:01:00Z",
                    classification="internal",
                    confidence="high",
                    source=synthetic_source(),
                    provenance=synthetic_provenance(),
                    proposed_record=changed,
                )
            with self.assertRaisesRegex(ContractError, "human approval"):
                promote_candidate(
                    config,
                    first["id"],
                    reviewer_role="maintainer",
                    promoted_at="2026-07-27T12:02:00Z",
                )

            promoted = promote_candidate(
                config,
                first["id"],
                reviewer_role="maintainer",
                promoted_at="2026-07-27T12:02:00Z",
                human_approved=True,
            )
            replayed_promotion = promote_candidate(
                config,
                first["id"],
                reviewer_role="maintainer",
                promoted_at="2026-07-27T12:03:00Z",
                human_approved=True,
            )

            self.assertEqual(promoted, replayed_promotion)
            self.assertEqual(promoted["status"], "promoted")
            self.assertTrue(audit_repository(config).ok)
            self.assertTrue(audit_continuity(config).ok)

    def test_receipt_evaluation_and_writeback_survive_backup_restore(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            config = project(base / "memory")
            source_decision = decision("DEC-context-source")
            source_decision["decision"] = (
                "Use synthetic continuity evidence for local verification."
            )
            put_record(config, source_decision)
            query = memory_query(
                "synthetic continuity evidence",
                terms=["continuity", "evidence"],
                record_kinds=["decision"],
                statuses=["accepted"],
            )
            bundle = compile_context(config, query)
            store_context_bundle(config, bundle)

            execution = orchestration_documents()
            execution[5]["context_sha256"] = bundle["context_sha256"]
            append_orchestration_documents(config, execution)
            append_workflow_documents(config, workflow_records())

            state_revision = sha256_file(config.state_file)
            state = read_json(
                config.state_file,
                max_bytes=config.limits.max_record_bytes,
            )
            result_fact = fact("FCT-writeback-result", value="verified-result")
            state["updated_at"] = "2026-07-27T12:20:00Z"
            state["references"] = ["FCT-writeback-result"]
            state["items"] = [
                {
                    "source_id": "FCT-writeback-result",
                    "summary": "Synthetic write-back is complete.",
                    "status": "done",
                }
            ]
            plan = {
                "schema_version": 1,
                "kind": "mutation_plan",
                "id": "MPL-synthetic-writeback",
                "status": "proposed",
                "source_receipt_id": "RCP-root",
                "evaluation_id": "EVL-root",
                "context_sha256": bundle["context_sha256"],
                "requires_human": True,
                "created_at": "2026-07-27T12:15:00Z",
                "changes": [
                    {
                        "operation": "create",
                        "record_id": "FCT-writeback-result",
                        "expected_sha256": None,
                        "proposed_record_sha256": sha256_bytes(
                            canonical_json_bytes(result_fact)
                        ),
                        "proposed_record": result_fact,
                    },
                    {
                        "operation": "state_update",
                        "record_id": "STATE-current",
                        "expected_sha256": state_revision,
                        "proposed_record_sha256": sha256_bytes(
                            canonical_json_bytes(state)
                        ),
                        "proposed_record": state,
                    },
                ],
                "reviewer_role": None,
                "applied_at": None,
                "transaction_id": None,
                "result_sha256_by_id": None,
            }
            store_mutation_plan(config, plan)
            with self.assertRaisesRegex(ContractError, "human approval"):
                apply_mutation_plan(
                    config,
                    plan["id"],
                    reviewer_role="maintainer",
                    applied_at="2026-07-27T12:21:00Z",
                )

            applied = apply_mutation_plan(
                config,
                plan["id"],
                reviewer_role="maintainer",
                applied_at="2026-07-27T12:21:00Z",
                human_approved=True,
            )
            replay = apply_mutation_plan(
                config,
                plan["id"],
                reviewer_role="maintainer",
                applied_at="2026-07-27T12:22:00Z",
                human_approved=True,
            )
            self.assertEqual(applied, replay)
            self.assertEqual(applied["status"], "applied")
            self.assertTrue(audit_repository(config).ok)
            self.assertTrue(audit_continuity(config).ok)

            backup = create_backup(config, label="continuity")
            restore_backup(backup, base / "restored")
            restored = load_config(base / "restored")
            self.assertTrue(audit_repository(restored).ok)
            restored_report = audit_continuity(restored)
            self.assertTrue(restored_report.ok, restored_report.errors)
            self.assertEqual(restored_report.context_count, 1)
            self.assertEqual(restored_report.episode_count, 2)
            self.assertEqual(restored_report.plan_count, 1)


if __name__ == "__main__":
    unittest.main()
