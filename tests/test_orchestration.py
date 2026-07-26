from __future__ import annotations

import unittest
from copy import deepcopy

from stateweave.core.errors import ContractError
from stateweave.orchestration import (
    audit_execution,
    manifest_digest,
    route_task,
    topological_order,
)

ZERO_HASH = "0" * 64
ONE_HASH = "1" * 64


def orchestration_documents() -> list[dict[str, object]]:
    root_manifest: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "input_manifest",
        "id": "INP-root",
        "task_id": "TSK-root",
        "created_at": "2026-07-25T12:00:00Z",
        "resources": [
            {
                "name": "source-tree",
                "locator": "synthetic/source",
                "sha256": ZERO_HASH,
                "classification": "internal",
            }
        ],
        "parameters": {"mode": "synthetic"},
    }
    child_manifest: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "input_manifest",
        "id": "INP-child",
        "task_id": "TSK-child",
        "created_at": "2026-07-25T12:05:00Z",
        "resources": [],
        "parameters": {},
    }
    return [
        {
            "schema_version": "1.0",
            "kind": "task",
            "id": "TSK-root",
            "title": "Inspect",
            "objective": "Inspect a fully synthetic source tree.",
            "dependencies": [],
            "required_capabilities": ["repository-read"],
            "risk": "low",
            "input_manifest_id": "INP-root",
            "expected_outputs": ["findings"],
        },
        {
            "schema_version": "1.0",
            "kind": "task",
            "id": "TSK-child",
            "title": "Validate",
            "objective": "Validate the synthetic inspection result.",
            "dependencies": ["TSK-root"],
            "required_capabilities": ["repository-read", "test-run"],
            "risk": "moderate",
            "input_manifest_id": "INP-child",
            "expected_outputs": ["test-report"],
        },
        root_manifest,
        child_manifest,
        {
            "schema_version": "1.0",
            "kind": "worker",
            "id": "WKR-general",
            "role": "contributor",
            "capabilities": ["repository-read", "test-run"],
            "risk_ceiling": "moderate",
            "priority": 20,
            "runtime_adapter": "local",
        },
        {
            "schema_version": "1.0",
            "kind": "execution_receipt",
            "id": "RCP-root",
            "task_id": "TSK-root",
            "worker_id": "WKR-general",
            "status": "succeeded",
            "started_at": "2026-07-25T12:10:00Z",
            "finished_at": "2026-07-25T12:11:00Z",
            "input_manifest_sha256": manifest_digest(root_manifest),
            "outputs": [{"name": "findings", "sha256": ONE_HASH}],
            "runtime_observation": {
                "adapter": "local",
                "implementation": "synthetic-runner",
                "model_id": None,
            },
            "metrics": {
                "duration_ms": 60000,
                "input_units": None,
                "output_units": None,
            },
        },
        {
            "schema_version": "1.0",
            "kind": "evaluation",
            "id": "EVL-root",
            "receipt_id": "RCP-root",
            "outcome": "pass",
            "checks": [
                {
                    "name": "contract-test",
                    "status": "pass",
                    "evidence": "tests/test_orchestration.py",
                }
            ],
            "evaluated_at": "2026-07-25T12:12:00Z",
        },
    ]


class OrchestrationTests(unittest.TestCase):
    def test_execution_graph_manifest_receipt_and_evaluation_are_valid(self) -> None:
        report = audit_execution(orchestration_documents())
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(report.task_order, ["TSK-root", "TSK-child"])

    def test_topological_order_rejects_missing_edges_and_cycles(self) -> None:
        tasks = orchestration_documents()[:2]
        missing = deepcopy(tasks)
        missing[1]["dependencies"] = ["TSK-missing"]
        with self.assertRaises(ContractError):
            topological_order(missing)

        cyclic = deepcopy(tasks)
        cyclic[0]["dependencies"] = ["TSK-child"]
        with self.assertRaises(ContractError):
            topological_order(cyclic)

    def test_routing_is_capability_risk_and_priority_aware(self) -> None:
        task = orchestration_documents()[1]
        workers = [
            {
                "schema_version": "1.0",
                "kind": "worker",
                "id": "WKR-second",
                "role": "contributor",
                "capabilities": ["repository-read", "test-run"],
                "risk_ceiling": "high",
                "priority": 20,
                "runtime_adapter": "local",
            },
            {
                "schema_version": "1.0",
                "kind": "worker",
                "id": "WKR-first",
                "role": "contributor",
                "capabilities": ["repository-read", "test-run"],
                "risk_ceiling": "moderate",
                "priority": 10,
                "runtime_adapter": "local",
            },
        ]

        selected = route_task(task, workers)

        self.assertEqual(selected["id"], "WKR-first")

    def test_receipt_detects_input_drift_and_reverse_timestamps(self) -> None:
        documents = orchestration_documents()
        receipt = documents[5]
        receipt["input_manifest_sha256"] = ZERO_HASH
        receipt["finished_at"] = "2026-07-25T12:09:00Z"

        report = audit_execution(documents)

        joined = "\n".join(report.errors)
        self.assertIn("input manifest digest does not match", joined)
        self.assertIn("finished_at precedes started_at", joined)

    def test_unknown_fields_and_dangling_evaluation_fail_closed(self) -> None:
        documents = orchestration_documents()
        documents[0]["unknown"] = True
        documents[-1]["receipt_id"] = "RCP-missing"

        report = audit_execution(documents)

        joined = "\n".join(report.errors)
        self.assertIn("Additional properties are not allowed", joined)
        self.assertIn("missing execution receipt", joined)

    def test_malformed_reference_types_report_without_crashing(self) -> None:
        documents = orchestration_documents()
        documents[2]["task_id"] = []
        documents[5]["worker_id"] = []
        documents[6]["receipt_id"] = []

        report = audit_execution(documents)

        self.assertFalse(report.ok)
        self.assertTrue(report.errors)


if __name__ == "__main__":
    unittest.main()
