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

from stateweave.adoption import discover_project_config
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
from stateweave.onboarding import (
    apply_onboarding_plan,
    audit_onboarding,
    plan_onboarding,
)

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


if __name__ == "__main__":
    unittest.main()
