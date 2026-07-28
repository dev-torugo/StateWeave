from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from stateweave.adoption import (
    apply_project_adoption,
    discover_project_config,
    plan_project_adoption,
)
from stateweave.cli import main
from stateweave.continuity import (
    audit_continuity,
    capture_candidate,
    list_candidates,
    preview_candidate,
    promote_candidate,
    reject_candidate,
)
from stateweave.core.backup import create_backup, restore_backup
from stateweave.core.config import load_config
from stateweave.core.errors import ContractError, RecordError
from stateweave.core.io import canonical_json_bytes, sha256_bytes
from stateweave.core.project import put_record
from stateweave.onboarding import (
    apply_onboarding_plan,
    audit_onboarding,
    plan_onboarding,
)
from stateweave.onboarding import project as onboarding_project

from tests.helpers import fact, project
from tests.test_continuity import synthetic_provenance, synthetic_source


class OnboardingPlanTests(unittest.TestCase):
    def _host_project(self, root: Path) -> dict[str, bytes]:
        files = {
            "AGENTS.md": b"# Synthetic host instructions\n",
            ".gitignore": b"synthetic-cache/\n",
            ".git/HEAD": b"ref: refs/heads/synthetic\n",
            "src/app.py": b"print('synthetic')\n",
        }
        for relative, payload in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        return files

    def _rebind_plan(self, plan: dict[str, Any]) -> None:
        payload = {
            key: value
            for key, value in plan.items()
            if key not in {"id", "plan_sha256"}
        }
        digest = sha256_bytes(canonical_json_bytes(payload))
        plan["id"] = f"ONP-{digest}"
        plan["plan_sha256"] = digest

    def test_plan_is_read_only_hash_bound_and_explicit_about_risk(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = self._host_project(root)

            first = plan_onboarding(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="local",
            )
            second = plan_onboarding(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="local",
            )

            self.assertEqual(first, second)
            self.assertEqual(first["status"], "ready")
            self.assertEqual(first["id"], f"ONP-{first['plan_sha256']}")
            self.assertEqual(first["sidecar_policy"], "local")
            self.assertEqual(
                {item["code"] for item in first["states"]},
                {
                    "adoption-status",
                    "deployment-mode",
                    "host-content-inspection",
                    "sidecar-policy",
                },
            )
            self.assertIn(
                "local-sidecar-vcs-exposure",
                {item["code"] for item in first["risks"]},
            )
            self.assertEqual(
                first["pending_decisions"],
                [
                    {
                        "code": "confirm-apply",
                        "options": ["apply-reviewed-plan", "cancel"],
                        "requires_human_confirmation": True,
                    }
                ],
            )
            self.assertEqual(
                [item["sequence"] for item in first["actions"]],
                list(range(1, len(first["actions"]) + 1)),
            )
            self.assertNotIn("prompt", json.dumps(first["pending_decisions"]))
            self.assertNotIn("chat", json.dumps(first["pending_decisions"]))
            self.assertFalse((root / ".stateweave-project").exists())
            for relative, payload in expected.items():
                self.assertEqual((root / relative).read_bytes(), payload)

    def test_apply_preserves_host_and_backup_restore_preserves_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "host"
            root.mkdir()
            expected = self._host_project(root)
            plan = plan_onboarding(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="tracked",
            )

            with self.assertRaisesRegex(RecordError, "human confirmation"):
                apply_onboarding_plan(
                    root,
                    project_id="synthetic-host",
                    project_name="Synthetic Host",
                    sidecar_policy="tracked",
                    expected_plan_sha256=plan["plan_sha256"],
                    decided_at="2026-07-27T20:00:00Z",
                    reviewer_role="maintainer",
                    human_confirmed=False,
                )

            result = apply_onboarding_plan(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="tracked",
                expected_plan_sha256=plan["plan_sha256"],
                decided_at="2026-07-27T20:00:00Z",
                reviewer_role="maintainer",
                human_confirmed=True,
            )
            config = load_config(discover_project_config(root))

            self.assertEqual(result["status"], "onboarded")
            self.assertEqual(
                result["policy_decision"]["sidecar_policy"],
                "tracked",
            )
            report = audit_onboarding(config)
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(report.plan_count, 1)
            self.assertEqual(report.policy_decision_count, 1)
            completed_plan = plan_onboarding(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="tracked",
            )
            self.assertEqual(completed_plan["status"], "complete")
            self.assertEqual(completed_plan["pending_decisions"], [])
            conflicting_plan = plan_onboarding(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="defer",
            )
            self.assertEqual(conflicting_plan["status"], "blocked")
            self.assertIn(
                "immutable-policy-conflict",
                {item["code"] for item in conflicting_plan["risks"]},
            )
            with self.assertRaisesRegex(RecordError, "blocked"):
                apply_onboarding_plan(
                    root,
                    project_id="synthetic-host",
                    project_name="Synthetic Host",
                    sidecar_policy="defer",
                    expected_plan_sha256=conflicting_plan["plan_sha256"],
                    decided_at="2026-07-27T20:01:00Z",
                    reviewer_role="maintainer",
                    human_confirmed=True,
                )
            for relative, payload in expected.items():
                self.assertEqual((root / relative).read_bytes(), payload)

            archive = create_backup(config, label="onboarding")
            restored_root = base / "restored"
            restore_backup(archive, restored_root)
            restored = load_config(restored_root)
            restored_report = audit_onboarding(restored)
            self.assertTrue(restored_report.ok, restored_report.errors)
            self.assertEqual(restored_report.plan_count, 1)
            self.assertEqual(restored_report.policy_decision_count, 1)

    def test_defer_never_creates_sidecar_and_plan_drift_fails_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._host_project(root)
            deferred = plan_onboarding(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="defer",
            )
            self.assertEqual(
                deferred["pending_decisions"],
                [
                    {
                        "code": "confirm-defer",
                        "options": ["confirm-defer", "change-policy"],
                        "requires_human_confirmation": True,
                    }
                ],
            )
            result = apply_onboarding_plan(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="defer",
                expected_plan_sha256=deferred["plan_sha256"],
                decided_at="2026-07-27T20:00:00Z",
                reviewer_role="maintainer",
                human_confirmed=True,
            )
            self.assertEqual(result["status"], "deferred")
            self.assertFalse(result["mutated"])
            self.assertFalse((root / ".stateweave-project").exists())

            unrecorded_root = root / "unrecorded"
            unrecorded_root.mkdir()
            self._host_project(unrecorded_root)
            adoption = plan_project_adoption(
                unrecorded_root,
                project_id="synthetic-unrecorded",
                project_name="Synthetic Unrecorded",
            )
            apply_project_adoption(
                unrecorded_root,
                project_id="synthetic-unrecorded",
                project_name="Synthetic Unrecorded",
                expected_plan_sha256=adoption["plan_sha256"],
                adopted_at="2026-07-27T20:00:00Z",
                confirmed=True,
            )
            recordable = plan_onboarding(
                unrecorded_root,
                project_id="synthetic-unrecorded",
                project_name="Synthetic Unrecorded",
                sidecar_policy="tracked",
            )
            self.assertEqual(recordable["status"], "ready")
            self.assertEqual(
                [action["code"] for action in recordable["actions"]],
                ["review-plan", "record-sidecar-policy"],
            )
            unrecorded_defer = plan_onboarding(
                unrecorded_root,
                project_id="synthetic-unrecorded",
                project_name="Synthetic Unrecorded",
                sidecar_policy="defer",
            )
            self.assertEqual(unrecorded_defer["status"], "blocked")
            self.assertIn(
                "unrecorded-sidecar-policy",
                {item["code"] for item in unrecorded_defer["risks"]},
            )

            blocked_root = root / "blocked"
            blocked_root.mkdir()
            (blocked_root / ".stateweave-project").write_text(
                "synthetic conflict",
                encoding="utf-8",
            )
            blocked = plan_onboarding(
                blocked_root,
                project_id="synthetic-blocked",
                project_name="Synthetic Blocked",
                sidecar_policy="tracked",
            )
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(
                blocked["pending_decisions"],
                [
                    {
                        "code": "resolve-blocker",
                        "options": ["inspect-evidence", "abort"],
                        "requires_human_confirmation": True,
                    }
                ],
            )

            tracked = plan_onboarding(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="tracked",
            )
            (root / "new-synthetic-file.txt").write_text(
                "drift",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RecordError, "plan changed"):
                apply_onboarding_plan(
                    root,
                    project_id="synthetic-host",
                    project_name="Synthetic Host",
                    sidecar_policy="tracked",
                    expected_plan_sha256=tracked["plan_sha256"],
                    decided_at="2026-07-27T20:00:00Z",
                    reviewer_role="maintainer",
                    human_confirmed=True,
                )

    def test_codex_skill_bridge_runs_the_read_only_plan_contract(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        bridge = (
            repository
            / "plugins"
            / "stateweave-onboarding"
            / "skills"
            / "stateweave-onboarding"
            / "scripts"
            / "stateweave_onboarding.py"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = self._host_project(root)
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(repository / "src")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(bridge),
                    "onboarding-plan",
                    str(root),
                    "--id",
                    "synthetic-host",
                    "--name",
                    "Synthetic Host",
                    "--sidecar-policy",
                    "local",
                ],
                cwd=repository,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["sidecar_policy"], "local")
            self.assertFalse((root / ".stateweave-project").exists())
            for relative, content in expected.items():
                self.assertEqual((root / relative).read_bytes(), content)

    def test_semantic_plan_validation_rejects_rebound_inconsistencies(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._host_project(root)
            original = plan_onboarding(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="tracked",
            )
            mutations = {}

            adoption_digest = json.loads(json.dumps(original))
            adoption_digest["adoption_plan"]["existing_entry_count"] += 1
            mutations["nested adoption digest"] = adoption_digest

            identity = json.loads(json.dumps(original))
            identity["adoption_plan"]["project_name"] = "Different Synthetic Name"
            adoption_payload = {
                key: value
                for key, value in identity["adoption_plan"].items()
                if key != "plan_sha256"
            }
            identity["adoption_plan"]["plan_sha256"] = sha256_bytes(
                canonical_json_bytes(adoption_payload)
            )
            mutations["nested project identity"] = identity

            actions = json.loads(json.dumps(original))
            actions["actions"][1]["sequence"] = 1
            mutations["action sequence"] = actions

            pending = json.loads(json.dumps(original))
            pending["pending_decisions"] = []
            mutations["pending decisions"] = pending

            for label, mutated in mutations.items():
                with self.subTest(label=label):
                    self._rebind_plan(mutated)
                    errors = onboarding_project._validate_plan(mutated, label)
                    self.assertTrue(errors)
                    self.assertTrue(
                        any(
                            marker in "; ".join(errors)
                            for marker in (
                                "adoption plan digest",
                                "project name",
                                "not contiguous",
                                "pending decisions",
                            )
                        ),
                        errors,
                    )

    def test_audit_binds_plan_policy_configuration_and_reviewer(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._host_project(root)
            plan = plan_onboarding(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="tracked",
            )
            apply_onboarding_plan(
                root,
                project_id="synthetic-host",
                project_name="Synthetic Host",
                sidecar_policy="tracked",
                expected_plan_sha256=plan["plan_sha256"],
                decided_at="2026-07-27T20:00:00Z",
                reviewer_role="maintainer",
                human_confirmed=True,
            )
            config = load_config(discover_project_config(root))
            onboarding_root = config.extensions_dir / "onboarding"
            old_plan_path = onboarding_root / "plans" / f"{plan['id']}.json"
            foreign = json.loads(old_plan_path.read_text(encoding="utf-8"))
            foreign["project_id"] = "synthetic-foreign"
            foreign["project_name"] = "Synthetic Foreign"
            foreign["adoption_plan"]["project_id"] = "synthetic-foreign"
            foreign["adoption_plan"]["project_name"] = "Synthetic Foreign"
            adoption_payload = {
                key: value
                for key, value in foreign["adoption_plan"].items()
                if key != "plan_sha256"
            }
            foreign["adoption_plan"]["plan_sha256"] = sha256_bytes(
                canonical_json_bytes(adoption_payload)
            )
            self._rebind_plan(foreign)
            old_plan_path.unlink()
            foreign_path = onboarding_root / "plans" / f"{foreign['id']}.json"
            foreign_path.write_bytes(canonical_json_bytes(foreign))

            policy_path = onboarding_root / "sidecar-policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["onboarding_plan_sha256"] = foreign["plan_sha256"]
            policy["adoption_plan_sha256"] = foreign["adoption_plan"]["plan_sha256"]
            policy["reviewer_role"] = "unconfigured-reviewer"
            policy_payload = {
                key: value
                for key, value in policy.items()
                if key not in {"id", "decision_sha256"}
            }
            policy_digest = sha256_bytes(canonical_json_bytes(policy_payload))
            policy["id"] = f"OBD-{policy_digest}"
            policy["decision_sha256"] = policy_digest
            policy_path.write_bytes(canonical_json_bytes(policy))

            report = audit_onboarding(config)
            self.assertFalse(report.ok)
            observed = "; ".join(report.errors)
            self.assertIn("plan project id does not match configuration", observed)
            self.assertIn("plan project name does not match configuration", observed)
            self.assertIn("project id differs from its plan", observed)
            self.assertIn("reviewer role is not configured", observed)


class CandidateInboxTests(unittest.TestCase):
    def _candidate(
        self,
        config: object,
        *,
        key: str,
        identifier: str,
        confidence: str = "high",
    ) -> dict[str, object]:
        return capture_candidate(
            config,
            idempotency_key=key,
            captured_at="2026-07-27T20:10:00Z",
            classification="internal",
            confidence=confidence,
            source=synthetic_source(),
            provenance=synthetic_provenance(),
            proposed_record=fact(identifier),
        )

    def test_list_filter_preview_reject_and_promote_are_governed(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            config = project(base / "memory")
            rejected = self._candidate(
                config,
                key="synthetic-reject",
                identifier="FCT-inbox-reject",
            )
            promoted = self._candidate(
                config,
                key="synthetic-promote",
                identifier="FCT-inbox-promote",
                confidence="medium",
            )

            pending = list_candidates(
                config,
                situation="pending",
                confidence="high",
            )
            self.assertEqual(pending["count"], 1)
            self.assertEqual(
                pending["candidates"][0]["candidate_id"],
                rejected["id"],
            )

            rejection_preview = preview_candidate(config, rejected["id"])
            self.assertEqual(rejection_preview["effective_situation"], "pending")
            with self.assertRaisesRegex(ContractError, "human approval"):
                reject_candidate(
                    config,
                    rejected["id"],
                    expected_preview_sha256=rejection_preview["preview_sha256"],
                    reason_code="out-of-scope",
                    reviewer_role="maintainer",
                    decided_at="2026-07-27T20:20:00Z",
                )
            decision = reject_candidate(
                config,
                rejected["id"],
                expected_preview_sha256=rejection_preview["preview_sha256"],
                reason_code="out-of-scope",
                reviewer_role="maintainer",
                decided_at="2026-07-27T20:20:00Z",
                human_approved=True,
            )
            replay = reject_candidate(
                config,
                rejected["id"],
                expected_preview_sha256=rejection_preview["preview_sha256"],
                reason_code="out-of-scope",
                reviewer_role="maintainer",
                decided_at="2026-07-27T20:20:00Z",
                human_approved=True,
            )
            self.assertEqual(decision, replay)
            with self.assertRaisesRegex(RecordError, "immutable decision"):
                reject_candidate(
                    config,
                    rejected["id"],
                    expected_preview_sha256=rejection_preview["preview_sha256"],
                    reason_code="duplicate",
                    reviewer_role="maintainer",
                    decided_at="2026-07-27T20:20:00Z",
                    human_approved=True,
                )
            self.assertNotIn("prompt", json.dumps(decision))
            self.assertNotIn("chat", json.dumps(decision))
            self.assertEqual(
                list_candidates(config, situation="rejected")["count"],
                1,
            )
            with self.assertRaisesRegex(ContractError, "was rejected"):
                promote_candidate(
                    config,
                    rejected["id"],
                    reviewer_role="maintainer",
                    promoted_at="2026-07-27T20:21:00Z",
                    expected_preview_sha256=rejection_preview["preview_sha256"],
                    human_approved=True,
                )

            promotion_preview = preview_candidate(config, promoted["id"])
            with self.assertRaisesRegex(ContractError, "reviewed preview"):
                promote_candidate(
                    config,
                    promoted["id"],
                    reviewer_role="maintainer",
                    promoted_at="2026-07-27T20:22:00Z",
                    human_approved=True,
                )
            promoted_result = promote_candidate(
                config,
                promoted["id"],
                reviewer_role="maintainer",
                promoted_at="2026-07-27T20:22:00Z",
                expected_preview_sha256=promotion_preview["preview_sha256"],
                human_approved=True,
            )
            self.assertEqual(promoted_result["status"], "promoted")
            self.assertEqual(
                list_candidates(config, situation="promoted")["count"],
                1,
            )
            report = audit_continuity(config)
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(report.rejection_count, 1)

            archive = create_backup(config, label="candidate-inbox")
            restore_backup(archive, base / "restored")
            restored = load_config(base / "restored")
            restored_report = audit_continuity(restored)
            self.assertTrue(restored_report.ok, restored_report.errors)
            self.assertEqual(restored_report.rejection_count, 1)

    def test_tampered_rejection_is_reported_and_cli_lists_effective_state(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = project(root / "memory")
            candidate = self._candidate(
                config,
                key="synthetic-tamper",
                identifier="FCT-inbox-tamper",
            )
            preview = preview_candidate(config, candidate["id"])
            reject_candidate(
                config,
                candidate["id"],
                expected_preview_sha256=preview["preview_sha256"],
                reason_code="duplicate",
                reviewer_role="maintainer",
                decided_at="2026-07-27T20:30:00Z",
                human_approved=True,
            )

            output = StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "candidate-list",
                        "--config",
                        str(config.root),
                        "--situation",
                        "rejected",
                    ]
                )
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["count"], 1)

            path = (
                config.extensions_dir
                / "continuity"
                / "candidate-decisions"
                / f"{candidate['id']}.json"
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["reason_code"] = "other"
            path.write_text(json.dumps(payload), encoding="utf-8")
            report = audit_continuity(config)
            self.assertFalse(report.ok)
            self.assertIn("digest does not match", "; ".join(report.errors))

            error = StringIO()
            with redirect_stderr(error):
                refused = main(
                    [
                        "reject-candidate",
                        candidate["id"],
                        "--config",
                        str(config.root),
                        "--expected-preview-sha256",
                        preview["preview_sha256"],
                        "--reason-code",
                        "duplicate",
                        "--reviewer-role",
                        "maintainer",
                        "--decided-at",
                        "2026-07-27T20:30:00Z",
                    ]
                )
            self.assertEqual(refused, 2)

    def test_concurrent_promotion_and_rejection_have_one_durable_winner(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            candidate = self._candidate(
                config,
                key="synthetic-decision-race",
                identifier="FCT-inbox-decision-race",
            )
            preview = preview_candidate(config, candidate["id"])

            def promote() -> tuple[str, str]:
                try:
                    result = promote_candidate(
                        config,
                        candidate["id"],
                        reviewer_role="maintainer",
                        promoted_at="2026-07-27T20:40:00Z",
                        expected_preview_sha256=preview["preview_sha256"],
                        human_approved=True,
                    )
                    return "promoted", result["id"]
                except ContractError as exc:
                    return "refused", str(exc)

            def reject() -> tuple[str, str]:
                try:
                    result = reject_candidate(
                        config,
                        candidate["id"],
                        expected_preview_sha256=preview["preview_sha256"],
                        reason_code="out-of-scope",
                        reviewer_role="maintainer",
                        decided_at="2026-07-27T20:40:00Z",
                        human_approved=True,
                    )
                    return "rejected", result["id"]
                except ContractError as exc:
                    return "refused", str(exc)

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = [
                    future.result(timeout=10)
                    for future in (
                        executor.submit(promote),
                        executor.submit(reject),
                    )
                ]

            winners = [status for status, _ in outcomes if status != "refused"]
            self.assertEqual(len(winners), 1, outcomes)
            self.assertIn(winners[0], {"promoted", "rejected"})
            inbox = list_candidates(config)
            self.assertEqual(inbox["count"], 1)
            self.assertEqual(
                inbox["candidates"][0]["effective_situation"],
                winners[0],
            )
            report = audit_continuity(config)
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(
                report.rejection_count,
                1 if winners[0] == "rejected" else 0,
            )

    def test_rejection_cannot_follow_an_interrupted_promotion_effect(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            candidate = self._candidate(
                config,
                key="synthetic-interrupted-promotion",
                identifier="FCT-inbox-interrupted-promotion",
            )
            put_record(
                config,
                candidate["proposed_record"],
                idempotency_key=f"promote:{candidate['id']}",
            )
            reconciliation = preview_candidate(config, candidate["id"])
            self.assertEqual(
                reconciliation["effective_situation"],
                "promotion-needs-reconciliation",
            )
            with self.assertRaisesRegex(ContractError, "requires reconciliation"):
                reject_candidate(
                    config,
                    candidate["id"],
                    expected_preview_sha256=reconciliation["preview_sha256"],
                    reason_code="out-of-scope",
                    reviewer_role="maintainer",
                    decided_at="2026-07-27T20:50:00Z",
                    human_approved=True,
                )
            promoted = promote_candidate(
                config,
                candidate["id"],
                reviewer_role="maintainer",
                promoted_at="2026-07-27T20:51:00Z",
                expected_preview_sha256=reconciliation["preview_sha256"],
                human_approved=True,
            )
            self.assertEqual(promoted["status"], "promoted")
            self.assertTrue(audit_continuity(config).ok)

            rejected = self._candidate(
                config,
                key="synthetic-rejected-effect",
                identifier="FCT-inbox-rejected-effect",
            )
            rejected_preview = preview_candidate(config, rejected["id"])
            reject_candidate(
                config,
                rejected["id"],
                expected_preview_sha256=rejected_preview["preview_sha256"],
                reason_code="out-of-scope",
                reviewer_role="maintainer",
                decided_at="2026-07-27T20:52:00Z",
                human_approved=True,
            )
            put_record(
                config,
                rejected["proposed_record"],
                idempotency_key="synthetic-invalid-post-rejection-effect",
            )
            report = audit_continuity(config)
            self.assertFalse(report.ok)
            self.assertIn(
                "rejected candidate result already exists",
                "; ".join(report.errors),
            )


if __name__ == "__main__":
    unittest.main()
