from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.core.errors import ContractError
from stateweave.policy import authorize_effect, load_policy_pack
from stateweave.workflow import audit_workflow

ROOT = Path(__file__).resolve().parents[1]


def workflow_records() -> list[dict[str, object]]:
    return [
        {
            "schema_version": "1.0",
            "kind": "work_request",
            "id": "WRK-synthetic",
            "title": "Synthetic work",
            "objective": "Exercise the portable workflow contract.",
            "requester_role": "maintainer",
            "assignee_role": "contributor",
            "risk": "moderate",
            "requested_effects": ["write-repository"],
            "status": "accepted",
            "created_at": "2026-07-25T12:00:00Z",
        },
        {
            "schema_version": "1.0",
            "kind": "handoff",
            "id": "HND-synthetic",
            "work_request_id": "WRK-synthetic",
            "producer_role": "contributor",
            "receiver_role": "maintainer",
            "summary": "Synthetic implementation and verification are ready.",
            "evidence": [
                {
                    "kind": "test",
                    "locator": "tests/test_synthetic.py",
                    "strength": "synthetic",
                }
            ],
            "limitations": ["No external system was contacted."],
            "created_at": "2026-07-25T12:30:00Z",
        },
        {
            "schema_version": "1.0",
            "kind": "acceptance",
            "id": "ACC-synthetic",
            "work_request_id": "WRK-synthetic",
            "handoff_id": "HND-synthetic",
            "decider_role": "maintainer",
            "outcome": "accepted",
            "notes": "Synthetic evidence satisfies the local contract.",
            "decided_at": "2026-07-25T13:00:00Z",
        },
    ]


class WorkflowAndPolicyTests(unittest.TestCase):
    def test_complete_workflow_chain_is_valid(self) -> None:
        policy = load_policy_pack(ROOT / "examples/research-lab/policy-pack.json")
        report = audit_workflow(workflow_records(), roles=policy.roles)
        self.assertTrue(report.ok, report.errors)

    def test_dangling_and_mismatched_lifecycle_fails_closed(self) -> None:
        records = workflow_records()
        records.pop()
        records[0]["status"] = "accepted"
        records[1]["work_request_id"] = "WRK-missing"

        report = audit_workflow(
            records,
            roles=("maintainer", "reviewer", "contributor"),
        )

        joined = "\n".join(report.errors)
        self.assertIn("missing work request", joined)
        self.assertIn("requires a handoff", joined)

    def test_schema_and_duplicate_ids_are_both_reported(self) -> None:
        records = workflow_records()
        invalid = deepcopy(records[0])
        invalid["unexpected"] = True
        records.append(invalid)

        report = audit_workflow(
            records,
            roles=("maintainer", "reviewer", "contributor"),
        )

        joined = "\n".join(report.errors)
        self.assertIn("Additional properties are not allowed", joined)
        self.assertIn("duplicate workflow id", joined)

    def test_malformed_role_and_status_types_report_without_crashing(self) -> None:
        records = workflow_records()
        records[0]["requester_role"] = []
        records[0]["status"] = []

        report = audit_workflow(
            records,
            roles=("maintainer", "reviewer", "contributor"),
        )

        self.assertFalse(report.ok)
        self.assertTrue(report.errors)

    def test_human_gate_cannot_be_bypassed_by_role_allowlist(self) -> None:
        policy = load_policy_pack(ROOT / "examples/research-lab/policy-pack.json")

        blocked = authorize_effect(
            policy,
            role="maintainer",
            effect="publish-artifact",
        )
        approved = authorize_effect(
            policy,
            role="maintainer",
            effect="publish-artifact",
            human_approved=True,
        )

        self.assertFalse(blocked.allowed)
        self.assertTrue(blocked.requires_human)
        self.assertTrue(approved.allowed)

    def test_unknown_role_in_policy_mapping_is_rejected(self) -> None:
        source = ROOT / "examples/research-lab/policy-pack.json"
        payload = source.read_text(encoding="utf-8").replace(
            '"maintainer": "critical"',
            '"maintainer": "critical", "ghost-role": "low"',
        )
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "policy.json"
            target.write_text(payload, encoding="utf-8")
            with self.assertRaises(ContractError):
                load_policy_pack(target)


if __name__ == "__main__":
    unittest.main()
