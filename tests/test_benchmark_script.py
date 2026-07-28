from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.benchmark_context import (
    byte_summary,
    duration_summary,
    evaluation_size,
    parse_sizes,
    positive_integer,
    quality_gate,
    retrieval_cases,
    retrieval_metrics,
)


class BenchmarkScriptTests(unittest.TestCase):
    def test_small_evaluation_reports_quality_bytes_and_concurrency(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(repository / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/benchmark_context.py",
                "--sizes",
                "10",
                "--repeats",
                "1",
                "--evaluation-size",
                "101",
                "--concurrency-size",
                "10",
                "--concurrency-operations",
                "1",
            ],
            cwd=repository,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        result = payload["results"][0]
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(
            payload["size_semantics"],
            "total_records_including_state",
        )
        self.assertEqual(result["records"], 10)
        self.assertEqual(result["records_after_mutation"], 10)
        self.assertEqual(
            result["mutation_operation"],
            "revisioned_state_overwrite",
        )
        self.assertEqual(result["selected_items"], 8)
        self.assertGreater(result["context_scan"]["p95_ms"], 0)
        self.assertGreater(result["context_indexed"]["p95_ms"], 0)

        evaluation = payload["retrieval_evaluation"]
        self.assertEqual(evaluation["query_count"], 30)
        self.assertEqual(
            evaluation["corpus"],
            {
                "records_including_state": 101,
                "topics": 10,
                "relevant_records": 40,
                "hard_negative_records": 20,
                "filler_records": 40,
                "relationship_edges": 0,
            },
        )
        self.assertEqual(evaluation["ground_truth_judgments"], 120)
        self.assertTrue(evaluation["scan_index_equivalent"])
        self.assertEqual(
            evaluation["scan"]["metrics"],
            evaluation["index"]["metrics"],
        )
        self.assertEqual(
            evaluation["scan"]["metrics"],
            {
                "recall_at_1": 0.25,
                "recall_at_4": 1.0,
                "recall_at_8": 1.0,
                "precision_at_8": 0.5,
                "mrr": 1.0,
            },
        )
        self.assertEqual(
            evaluation["quality_gate"]["thresholds"],
            {
                "recall_at_8": 0.8,
                "precision_at_8": 0.5,
                "mrr": 0.7,
            },
        )
        self.assertTrue(evaluation["quality_gate"]["passed"])
        self.assertTrue(evaluation["quality_gate"]["scan"]["passed"])
        self.assertTrue(evaluation["quality_gate"]["index"]["passed"])
        for access_path in ("scan", "index"):
            measured = evaluation[access_path]
            self.assertEqual(len(measured["queries"]), 30)
            self.assertGreater(
                measured["bytes"]["selected_items"]["total"],
                0,
            )
            self.assertGreater(
                measured["bytes"]["complete_bundle"]["total"],
                measured["bytes"]["selected_items"]["total"],
            )
            self.assertTrue(
                all(
                    outcome["hard_negative_ranks"] == [5, 6]
                    for outcome in measured["queries"]
                )
            )

        concurrency = payload["concurrency"]
        self.assertEqual(concurrency["records_including_state"], 10)
        self.assertTrue(concurrency["scan_index_equivalent"])
        self.assertEqual(
            [item["access_path"] for item in concurrency["access_paths"]],
            ["scan", "index"],
        )
        for access_path in concurrency["access_paths"]:
            workloads = access_path["workloads"]
            self.assertEqual(
                [
                    (item["readers"], item["writers"])
                    for item in workloads
                ],
                [(1, 0), (4, 0), (8, 0), (7, 1)],
            )
            for workload in workloads:
                self.assertGreater(
                    workload["throughput_ops_per_second"],
                    0,
                )
                self.assertGreater(
                    workload["reader_latency"]["p95_ms"],
                    0,
                )
            self.assertIsNotNone(workloads[-1]["writer_latency"])
        self.assertTrue(
            concurrency["access_paths"][1]["index_valid_after"]
        )
        self.assertEqual(
            len(
                {
                    workload["context_bundle_id"]
                    for access_path in concurrency["access_paths"]
                    for workload in access_path["workloads"]
                }
            ),
            1,
        )

    def test_metric_helpers_reject_invalid_inputs(self) -> None:
        for value in ("0", "-1", "1,0", "one"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parse_sizes(value)
        for value in ("0", "-1", "one"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    positive_integer(value)
        for value in ("0", "60", "one"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    evaluation_size(value)
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            retrieval_metrics([])
        with self.assertRaisesRegex(
            ValueError,
            "relevant_ids must contain",
        ):
            retrieval_metrics(
                [{"relevant_ids": [], "retrieved_ids": []}]
            )
        with self.assertRaisesRegex(ValueError, "finite"):
            duration_summary([math.nan])
        with self.assertRaisesRegex(ValueError, "non-negative integers"):
            byte_summary([-1])
        with self.assertRaisesRegex(ValueError, "missing gate inputs"):
            quality_gate({"recall_at_8": 1.0})

    def test_quality_gate_reports_failed_thresholds(self) -> None:
        gate = quality_gate(
            {
                "recall_at_8": 0.79,
                "precision_at_8": 0.50,
                "mrr": 0.69,
            }
        )

        self.assertFalse(gate["passed"])
        self.assertEqual(
            gate["failed_metrics"],
            ["recall_at_8", "mrr"],
        )

    def test_duplicate_retrieval_ids_do_not_inflate_metrics(self) -> None:
        metrics = retrieval_metrics(
            [
                {
                    "relevant_ids": ["FCT-relevant"],
                    "retrieved_ids": ["FCT-relevant"] * 8,
                },
                {
                    "relevant_ids": ["FCT-late"],
                    "retrieved_ids": ["FCT-decoy", "FCT-late"],
                },
            ]
        )

        self.assertEqual(metrics["recall_at_1"], 0.5)
        self.assertEqual(metrics["recall_at_4"], 1.0)
        self.assertEqual(metrics["recall_at_8"], 1.0)
        self.assertEqual(metrics["precision_at_8"], 0.125)
        self.assertEqual(metrics["mrr"], 0.75)

    def test_retrieval_fixture_has_unique_ground_truth_and_negatives(
        self,
    ) -> None:
        cases = retrieval_cases()
        relevant = [
            identifier
            for case in cases
            for identifier in case.relevant_ids
        ]
        negatives = [
            identifier
            for case in cases
            for identifier in case.hard_negative_ids
        ]

        self.assertEqual(len(cases), 30)
        self.assertTrue(
            all(len(case.relevant_ids) == 4 for case in cases)
        )
        self.assertTrue(
            all(len(case.hard_negative_ids) == 2 for case in cases)
        )
        self.assertEqual(len(set(relevant)), 40)
        self.assertEqual(len(set(negatives)), 20)
        self.assertTrue(set(relevant).isdisjoint(negatives))


if __name__ == "__main__":
    unittest.main()
