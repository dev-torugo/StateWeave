from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "run_codex_value_experiment.py"
)
SPEC = importlib.util.spec_from_file_location("codex_value_experiment", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
experiment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(experiment)
NUMPY_AVAILABLE = importlib.util.find_spec("numpy") is not None


MODULE_SOURCE = """\
import numpy as np


def merge_threat_arrays(threat_1_arr, threat_2_arr, out_nodata=-9999):
    threat_1_valid = (threat_1_arr >= 1) & (threat_1_arr <= 3)
    threat_2_valid = (threat_2_arr >= 1) & (threat_2_arr <= 3)
    both_valid = threat_1_valid & threat_2_valid
    out_arr = np.full(threat_1_arr.shape, out_nodata, dtype=np.int16)
    out_arr[both_valid] = np.maximum(threat_1_arr[both_valid], threat_2_arr[both_valid])
    out_arr[threat_1_valid & ~threat_2_valid] = threat_1_arr[threat_1_valid & ~threat_2_valid]
    out_arr[threat_2_valid & ~threat_1_valid] = threat_2_arr[threat_2_valid & ~threat_1_valid]
    return out_arr
"""


def source_project(root: Path) -> Path:
    module = root / experiment.ALLOWLISTED_MODULE
    module.parent.mkdir(parents=True)
    module.write_text(MODULE_SOURCE, encoding="utf-8")
    (root / "outputs").mkdir()
    (root / "outputs" / "operational.gpkg").write_bytes(b"not-read")
    return root


def gate_run(
    task: str,
    arm: str,
    repetition: int,
    *,
    success: bool,
    uncached_input_tokens: int,
    evaluator_extra: bool = False,
) -> dict[str, object]:
    evaluator: dict[str, object] = {
        "sha256": "e" * 64,
        "exit_code": 0 if success else 1,
        "duration_ms": 1,
        "passed": success,
    }
    if evaluator_extra:
        evaluator["details"] = "must-not-be-persisted"
    return {
        "task": task,
        "arm": arm,
        "repetition": repetition,
        "success": success,
        "execution": {
            "uncached_input_tokens": uncached_input_tokens,
            "usage_valid": True,
        },
        "audits": {"memory": True, "continuity": True, "codex": True},
        "evaluator": evaluator,
    }


def full_campaign(
    *,
    bundle_successes: set[tuple[str, int]] | None = None,
    none_successes: set[tuple[str, int]] | None = None,
    evaluator_extra_cell: tuple[str, str, int] | None = None,
) -> list[dict[str, object]]:
    if bundle_successes is None:
        bundle_successes = {
            (task, repetition)
            for task in experiment.TASKS
            for repetition in range(1, 4)
        }
    if none_successes is None:
        none_successes = set()
    tokens = {"none": 100, "full": 1_000, "bundle": 600, "projection": 400}
    runs: list[dict[str, object]] = []
    for repetition in range(1, 4):
        for task in experiment.TASKS:
            for arm in experiment.ARMS:
                cell = (task, arm, repetition)
                success = (
                    (task, repetition) in bundle_successes
                    if arm == "bundle"
                    else (task, repetition) in none_successes
                    if arm == "none"
                    else False
                )
                runs.append(
                    gate_run(
                        task,
                        arm,
                        repetition,
                        success=success,
                        uncached_input_tokens=tokens[arm],
                        evaluator_extra=cell == evaluator_extra_cell,
                    )
                )
    return runs


class CodexValueExperimentTests(unittest.TestCase):
    def test_codex_cli_version_fixture_is_byte_stable_and_sanitized(self) -> None:
        version_bytes = b"codex-cli 9.8.7\n"
        with TemporaryDirectory() as temporary:
            binary = Path(temporary) / "codex_version_fixture.py"
            binary.write_text(
                "import sys\n"
                f"sys.stdout.buffer.write({version_bytes!r})\n"
                "sys.stderr.buffer.write(b'DO_NOT_PERSIST_VERSION_STDERR\\n')\n",
                encoding="utf-8",
            )

            first = experiment._codex_cli_observation([sys.executable, str(binary)])
            second = experiment._codex_cli_observation([sys.executable, str(binary)])

            expected = {
                "observed": True,
                "implementation": "codex-cli",
                "version": "9.8.7",
                "version_output_sha256": experiment.sha256_bytes(version_bytes),
            }
            self.assertEqual(first, expected)
            self.assertEqual(second, expected)
            encoded = json.dumps(first)
            self.assertNotIn(str(binary), encoded)
            self.assertNotIn("DO_NOT_PERSIST", encoded)

    def test_allowlist_rejects_symlink_and_ignores_unrelated_binary(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = source_project(root / "source")
            observed = experiment._validate_source(source)
            self.assertEqual(observed, source / experiment.ALLOWLISTED_MODULE)
            self.assertEqual(
                experiment._sha256_path(source / "outputs" / "operational.gpkg"),
                experiment._sha256_path(source / "outputs" / "operational.gpkg"),
            )

            linked = root / "linked"
            try:
                linked.symlink_to(source, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")
            with self.assertRaises(ValueError):
                experiment._validate_source(linked)

    def test_allowlist_rejects_symlinked_parent_component(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = source_project(root / "external")
            source = root / "source"
            source.mkdir()
            component = source / "caixa_ferramentas_interface"
            try:
                component.symlink_to(
                    external / "caixa_ferramentas_interface",
                    target_is_directory=True,
                )
            except OSError:
                self.skipTest("directory symlinks are unavailable")

            with self.assertRaises(ValueError):
                experiment._validate_source(source)

    def test_workspace_contains_no_test_or_oracle_visible_to_codex(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = source_project(root / "source")
            for task in experiment.TASKS:
                with self.subTest(task=task):
                    workspace = root / task
                    workspace.mkdir()
                    experiment._prepare_workspace(source, workspace, task)
                    self.assertEqual(
                        set(experiment._tree_snapshot(workspace)),
                        set(experiment.WORKSPACE_FILES),
                    )
                    self.assertTrue(experiment._workspace_ready_for_codex(workspace))
                    self.assertFalse(
                        any(
                            path.name.casefold().startswith("test")
                            or "oracle" in path.name.casefold()
                            for path in workspace.rglob("*")
                            if path.is_file()
                        )
                    )

            adversarial = root / "adversarial"
            adversarial.mkdir()
            experiment._prepare_workspace(source, adversarial, experiment.TASKS[0])
            (adversarial / "oracle_hint.txt").write_text("synthetic", encoding="utf-8")
            self.assertFalse(experiment._workspace_ready_for_codex(adversarial))

    def test_objectives_queries_and_prompts_are_opaque(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for task in experiment.TASKS:
                with self.subTest(task=task):
                    config, records = experiment._project_with_memory(root / task, task)
                    query = experiment._query(task)
                    bundle = __import__(
                        "stateweave.context",
                        fromlist=["compile_context"],
                    ).compile_context(config, query)
                    objective = experiment._task_objective(task)
                    self.assertIn(task, objective)
                    self.assertEqual(query["objective"], objective)
                    self.assertEqual(query["terms"], [task])
                    self.assertFalse(
                        experiment._contains_forbidden_prompt_term(objective)
                    )
                    self.assertFalse(experiment._contains_forbidden_prompt_term(query))
                    for arm in experiment.ARMS:
                        prompt = experiment._prompt(
                            task,
                            experiment._arm_context(arm, records, bundle),
                        )
                        self.assertFalse(
                            experiment._contains_forbidden_prompt_term(prompt),
                            (task, arm),
                        )

    @unittest.skipUnless(
        NUMPY_AVAILABLE,
        "the optional target-project NumPy dependency is unavailable",
    )
    def test_each_mutant_fails_and_fake_repair_passes_hidden_evaluator(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = source_project(root / "source")
            for task in experiment.TASKS:
                with self.subTest(task=task):
                    workspace = root / task
                    workspace.mkdir()
                    experiment._prepare_workspace(source, workspace, task)
                    mutant = experiment._run_hidden_evaluator(workspace, task)
                    experiment._apply_fake_success(workspace, task)
                    repaired = experiment._run_hidden_evaluator(workspace, task)
                    self.assertFalse(mutant["passed"])
                    self.assertNotEqual(mutant["exit_code"], 0)
                    self.assertTrue(repaired["passed"])
                    self.assertEqual(repaired["exit_code"], 0)

    def test_bundle_recovers_all_four_relevant_ids_for_each_request(self) -> None:
        expected = experiment._relevant_fact_ids()
        self.assertEqual(len(expected), 4)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for task in experiment.TASKS:
                with self.subTest(task=task):
                    config, _ = experiment._project_with_memory(root / task, task)
                    bundle = __import__(
                        "stateweave.context",
                        fromlist=["compile_context"],
                    ).compile_context(config, experiment._query(task))
                    selected = {item["id"] for item in bundle["items"]}
                    self.assertTrue(expected <= selected)
                    self.assertEqual(len(expected & selected), 4)

    def test_full_context_has_no_relevance_or_topic_labels(self) -> None:
        for task in experiment.TASKS:
            with self.subTest(task=task):
                records = experiment._memory_records(task)
                context = {"kind": "full_synthetic_memory", "records": records}
                self.assertEqual(len(records), 100)
                self.assertTrue(
                    all(set(record) == {"id", "text"} for record in records)
                )
                self.assertFalse(
                    experiment._contains_key(
                        context,
                        frozenset({"relevance", "topic"}),
                    )
                )

    def test_context_arms_are_bounded_and_projection_preserves_bindings(self) -> None:
        records = experiment._memory_records(experiment.TASKS[0])
        self.assertEqual(len(records), 100)
        self.assertLessEqual(
            len(experiment.canonical_json_bytes(records)),
            experiment.FULL_CONTEXT_LIMIT,
        )
        bundle = {
            "id": "CTX-" + "a" * 64,
            "context_sha256": "a" * 64,
            "items": [
                {
                    "id": "FCT-one",
                    "record_kind": "fact",
                    "revision_sha256": "b" * 64,
                    "score": 10,
                    "reasons": ["term:RQ-K7Q9:body"],
                    "content": {
                        "title": "Reference note",
                        "statement": "Synthetic evidence.",
                        "claim": {},
                        "sources": [],
                    },
                }
            ],
        }
        projection = experiment._project_context(bundle)
        self.assertEqual(projection["source_context_id"], bundle["id"])
        self.assertEqual(
            projection["items"][0]["revision_sha256"],
            "b" * 64,
        )
        self.assertNotIn("content", projection["items"][0])

    @unittest.skipUnless(
        NUMPY_AVAILABLE,
        "the optional target-project NumPy dependency is unavailable",
    )
    def test_one_repetition_dry_run_succeeds_12_of_12_but_gate_is_pilot_only(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            source = source_project(Path(temporary) / "source")
            args = argparse.Namespace(
                source_project=str(source),
                execute=False,
                model="unused",
                repetitions=1,
                timeout_seconds=30,
                fake_failure=False,
            )
            with patch.object(
                experiment,
                "_codex_cli_observation",
                return_value={
                    "observed": False,
                    "implementation": None,
                    "version": None,
                    "version_output_sha256": None,
                },
            ):
                report = experiment.run_experiment(args)

            self.assertEqual(report["mode"], "dry-run")
            self.assertEqual(report["design"]["planned_runs"], 12)
            self.assertEqual(len(report["runs"]), 12)
            self.assertEqual(sum(run["success"] for run in report["runs"]), 12)
            self.assertTrue(report["preflight"]["passed"])
            self.assertTrue(report["source_baseline"]["unchanged"])
            self.assertFalse(report["gate"]["passed"])
            self.assertFalse(report["gate"]["checks"]["real_three_repetition_campaign"])
            self.assertFalse(report["gate"]["checks"]["all_36_cells_complete"])
            self.assertEqual(
                report["usage_totals"]["input_tokens"],
                sum(run["execution"]["input_tokens"] for run in report["runs"]),
            )
            self.assertEqual(
                report["usage_totals"]["uncached_input_tokens"],
                sum(
                    run["execution"]["uncached_input_tokens"] for run in report["runs"]
                ),
            )
            self.assertEqual(
                report["limits"]["per_run_input_tokens"],
                experiment.MAX_INPUT_TOKENS_PER_RUN,
            )
            self.assertEqual(
                report["limits"]["per_run_uncached_input_tokens"],
                experiment.MAX_UNCACHED_INPUT_TOKENS_PER_RUN,
            )
            self.assertEqual(
                report["limits"]["token_threshold_enforcement"],
                "post-execution",
            )
            encoded = json.dumps(report)
            self.assertNotIn("<STATEWEAVE_CONTEXT>", encoded)
            self.assertNotIn("HIDDEN_EVALUATOR_SOURCE", encoded)
            self.assertTrue(
                all(
                    set(run["evaluator"])
                    == {"sha256", "exit_code", "duration_ms", "passed"}
                    for run in report["runs"]
                )
            )

    def test_gate_rejects_incomplete_campaign(self) -> None:
        runs = full_campaign()[:12]
        gate = experiment._gate(
            runs,
            execute=True,
            repetitions=3,
            stop_reason=None,
            preflight={"passed": True},
        )
        self.assertFalse(gate["passed"])
        self.assertFalse(gate["checks"]["all_36_cells_complete"])

    def test_gate_uses_bundle_as_primary_paired_binomial_evidence(self) -> None:
        gate = experiment._gate(
            full_campaign(),
            execute=True,
            repetitions=3,
            stop_reason=None,
            preflight={"passed": True},
        )
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["successes"]["bundle"], 9)
        self.assertEqual(gate["successes"]["none"], 0)
        self.assertEqual(gate["successes"]["full"], 0)
        self.assertEqual(gate["successes"]["projection"], 0)
        self.assertEqual(
            gate["bundle_successes_by_task"],
            {task: 3 for task in experiment.TASKS},
        )
        self.assertEqual(
            gate["paired_bundle_vs_none"],
            {
                "wins": 9,
                "losses": 0,
                "one_sided_binomial_pvalue": 1 / 512,
            },
        )
        self.assertTrue(gate["checks"]["bundle_paired_advantage"])
        self.assertEqual(gate["uncached_token_ratios"]["bundle_to_full"], 0.6)

    def test_aggregate_bundle_advantage_without_significant_pairing_fails(
        self,
    ) -> None:
        all_cells = [
            (task, repetition)
            for task in experiment.TASKS
            for repetition in range(1, 4)
        ]
        bundle_successes = set(all_cells[:-1])
        none_successes = {
            (task, repetition) for task in experiment.TASKS for repetition in (1, 2)
        }
        gate = experiment._gate(
            full_campaign(
                bundle_successes=bundle_successes,
                none_successes=none_successes,
            ),
            execute=True,
            repetitions=3,
            stop_reason=None,
            preflight={"passed": True},
        )
        self.assertEqual(gate["successes"]["bundle"], 8)
        self.assertEqual(gate["successes"]["none"], 6)
        self.assertTrue(gate["checks"]["bundle_success_at_least_8_of_9"])
        self.assertTrue(gate["checks"]["bundle_success_in_each_task"])
        self.assertEqual(gate["paired_bundle_vs_none"]["wins"], 2)
        self.assertEqual(gate["paired_bundle_vs_none"]["losses"], 0)
        self.assertEqual(
            gate["paired_bundle_vs_none"]["one_sided_binomial_pvalue"],
            0.25,
        )
        self.assertFalse(gate["checks"]["bundle_paired_advantage"])
        self.assertFalse(gate["passed"])

    def test_gate_requires_minimal_evaluator_evidence(self) -> None:
        gate = experiment._gate(
            full_campaign(evaluator_extra_cell=(experiment.TASKS[0], "bundle", 1)),
            execute=True,
            repetitions=3,
            stop_reason=None,
            preflight={"passed": True},
        )
        self.assertFalse(gate["checks"]["privacy"])
        self.assertFalse(gate["passed"])

    def test_raw_and_uncached_token_caps_are_independent(self) -> None:
        at_limit = {
            "execution": {
                "input_tokens": experiment.MAX_INPUT_TOKENS_PER_RUN,
                "uncached_input_tokens": (experiment.MAX_UNCACHED_INPUT_TOKENS_PER_RUN),
                "usage_valid": True,
            }
        }
        self.assertIsNone(
            experiment._token_stop_reason(
                at_limit,
                total_input_tokens=experiment.MAX_CAMPAIGN_INPUT_TOKENS,
                total_uncached_input_tokens=(
                    experiment.MAX_CAMPAIGN_UNCACHED_INPUT_TOKENS
                ),
            )
        )
        cases = (
            (
                experiment.MAX_INPUT_TOKENS_PER_RUN + 1,
                0,
                0,
                0,
                "per-run-input-token-cap",
            ),
            (
                0,
                experiment.MAX_UNCACHED_INPUT_TOKENS_PER_RUN + 1,
                0,
                0,
                "per-run-uncached-input-token-cap",
            ),
            (
                0,
                0,
                experiment.MAX_CAMPAIGN_INPUT_TOKENS + 1,
                0,
                "campaign-input-token-cap",
            ),
            (
                0,
                0,
                0,
                experiment.MAX_CAMPAIGN_UNCACHED_INPUT_TOKENS + 1,
                "campaign-uncached-input-token-cap",
            ),
        )
        for raw, uncached, total_raw, total_uncached, expected in cases:
            with self.subTest(expected=expected):
                run = {
                    "execution": {
                        "input_tokens": raw,
                        "uncached_input_tokens": uncached,
                        "usage_valid": True,
                    }
                }
                self.assertEqual(
                    experiment._token_stop_reason(
                        run,
                        total_input_tokens=total_raw,
                        total_uncached_input_tokens=total_uncached,
                    ),
                    expected,
                )

    def test_campaign_stops_after_each_observed_token_threshold(self) -> None:
        scenarios = (
            (
                "per-run-input-token-cap",
                1,
                experiment.MAX_INPUT_TOKENS_PER_RUN + 1,
                0,
                True,
            ),
            (
                "per-run-uncached-input-token-cap",
                1,
                experiment.MAX_UNCACHED_INPUT_TOKENS_PER_RUN + 1,
                experiment.MAX_UNCACHED_INPUT_TOKENS_PER_RUN + 1,
                True,
            ),
            (
                "campaign-input-token-cap",
                31,
                experiment.MAX_INPUT_TOKENS_PER_RUN,
                0,
                True,
            ),
            (
                "campaign-uncached-input-token-cap",
                31,
                experiment.MAX_UNCACHED_INPUT_TOKENS_PER_RUN,
                experiment.MAX_UNCACHED_INPUT_TOKENS_PER_RUN,
                True,
            ),
            (
                "invalid-token-usage",
                1,
                0,
                0,
                False,
            ),
        )
        with TemporaryDirectory() as temporary:
            source = source_project(Path(temporary) / "source")
            for expected_reason, expected_runs, raw, uncached, usage_valid in scenarios:
                with self.subTest(expected_reason=expected_reason):

                    def observed_run(
                        _source_project: Path,
                        *,
                        task_name: str,
                        arm: str,
                        repetition: int,
                        **_kwargs: object,
                    ) -> dict[str, object]:
                        return {
                            "task": task_name,
                            "arm": arm,
                            "repetition": repetition,
                            "success": True,
                            "execution": {
                                "input_tokens": raw,
                                "cached_input_tokens": raw - uncached,
                                "uncached_input_tokens": uncached,
                                "output_tokens": 7,
                                "reasoning_tokens": 3,
                                "usage_valid": usage_valid,
                            },
                            "audits": {
                                "memory": True,
                                "continuity": True,
                                "codex": True,
                            },
                            "evaluator": {
                                "sha256": "e" * 64,
                                "exit_code": 0,
                                "duration_ms": 1,
                                "passed": True,
                            },
                        }

                    args = argparse.Namespace(
                        source_project=str(source),
                        execute=True,
                        model="synthetic-model",
                        repetitions=3,
                        timeout_seconds=30,
                        fake_failure=False,
                    )
                    with (
                        patch.object(
                            experiment,
                            "_run_preflight",
                            return_value={"passed": True},
                        ),
                        patch.object(
                            experiment,
                            "_codex_cli_observation",
                            return_value={
                                "observed": True,
                                "implementation": "codex-cli",
                                "version": "9.8.7",
                                "version_output_sha256": "c" * 64,
                            },
                        ),
                        patch.object(
                            experiment,
                            "_one_run",
                            side_effect=observed_run,
                        ),
                    ):
                        report = experiment.run_experiment(args)

                    self.assertEqual(report["stop_reason"], expected_reason)
                    self.assertEqual(len(report["runs"]), expected_runs)
                    self.assertFalse(report["gate"]["passed"])
                    self.assertFalse(report["gate"]["checks"]["no_stop_reason"])
                    self.assertEqual(
                        report["usage_totals"],
                        {
                            "input_tokens": raw * expected_runs,
                            "cached_input_tokens": (raw - uncached) * expected_runs,
                            "uncached_input_tokens": uncached * expected_runs,
                            "output_tokens": 7 * expected_runs,
                            "reasoning_tokens": 3 * expected_runs,
                        },
                    )

    def test_cli_requires_explicit_execute_for_real_codex(self) -> None:
        payload = {
            "source_baseline": {"unchanged": True},
            "mode": "dry-run",
            "codex": {"model": None},
        }
        output = StringIO()
        with patch.object(experiment, "run_experiment", return_value=payload):
            with redirect_stdout(output):
                status = experiment.main(
                    [
                        "--source-project",
                        "/synthetic/source",
                        "--repetitions",
                        "1",
                    ]
                )
        observed = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(observed["mode"], "dry-run")
        self.assertIsNone(observed["codex"]["model"])

    def test_cli_refuses_output_inside_immutable_source(self) -> None:
        with TemporaryDirectory() as temporary:
            source = source_project(Path(temporary) / "source")
            error = StringIO()
            with (
                redirect_stdout(StringIO()),
                patch.object(experiment, "run_experiment") as mocked,
                self.assertRaises(SystemExit),
                redirect_stderr(error),
            ):
                experiment.main(
                    [
                        "--source-project",
                        str(source),
                        "--output",
                        str(source / "result.json"),
                    ]
                )
            mocked.assert_not_called()
            self.assertIn("outside the immutable source", error.getvalue())

    def test_silent_codex_process_is_killed_at_timeout(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "silent_codex_fixture.py"
            binary.write_text(
                "import time\n\ntime.sleep(10)\n",
                encoding="utf-8",
            )
            result = experiment._run_codex(
                root,
                "synthetic prompt",
                model="synthetic-model",
                timeout_seconds=1,
                command_prefix=[sys.executable, str(binary)],
            )
            self.assertTrue(result["timed_out"])
            self.assertNotEqual(result["exit_code"], 0)
            self.assertLess(result["duration_ms"], 5_000)

    def test_codex_jsonl_parser_discards_free_text_and_spoofed_usage(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "jsonl_codex_fixture.py"
            binary.write_text(
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'type': 'item.completed', 'item': "
                "{'type': 'agent_message', 'text': 'DO_NOT_PERSIST_JSONL', "
                "'input_tokens': 999999}}), flush=True)\n"
                "print(json.dumps({'type': 'turn.completed', 'usage': "
                "{'input_tokens': 10, 'cached_input_tokens': 2, "
                "'output_tokens': 3, 'reasoning_output_tokens': 1}}), flush=True)\n",
                encoding="utf-8",
            )
            result = experiment._run_codex(
                root,
                "synthetic prompt",
                model="synthetic-model",
                timeout_seconds=5,
                command_prefix=[sys.executable, str(binary)],
            )

            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["input_tokens"], 10)
            self.assertEqual(result["cached_input_tokens"], 2)
            self.assertEqual(result["uncached_input_tokens"], 8)
            self.assertTrue(result["usage_valid"])
            self.assertEqual(result["event_types"], {"turn.completed": 1})
            self.assertEqual(result["discarded_event_count"], 1)
            self.assertNotIn("DO_NOT_PERSIST", json.dumps(result))

    def test_codex_jsonl_parser_fails_closed_on_inconsistent_usage(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "jsonl_codex_invalid_usage_fixture.py"
            binary.write_text(
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'type': 'turn.completed', 'usage': "
                "{'input_tokens': 10, 'cached_input_tokens': 20, "
                "'output_tokens': 3, 'reasoning_output_tokens': 1}}), flush=True)\n",
                encoding="utf-8",
            )
            result = experiment._run_codex(
                root,
                "synthetic prompt",
                model="synthetic-model",
                timeout_seconds=5,
                command_prefix=[sys.executable, str(binary)],
            )

            self.assertFalse(result["usage_valid"])
            self.assertEqual(result["uncached_input_tokens"], 10)
            invalid_run = gate_run(
                experiment.TASKS[0],
                "bundle",
                1,
                success=True,
                uncached_input_tokens=10,
            )
            invalid_run["execution"]["usage_valid"] = False
            gate = experiment._gate(
                [invalid_run],
                execute=True,
                repetitions=3,
                stop_reason=None,
                preflight={"passed": True},
            )
            self.assertFalse(gate["checks"]["token_usage"])
            self.assertFalse(gate["passed"])

    def test_codex_jsonl_parser_rejects_missing_or_partial_usage(self) -> None:
        fixtures = {
            "missing": (
                "print(json.dumps({'type': 'item.completed', "
                "'item': {'type': 'agent_message'}}), flush=True)\n"
            ),
            "partial": (
                "print(json.dumps({'type': 'turn.completed', "
                "'usage': {'input_tokens': 10}}), flush=True)\n"
            ),
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, event_source in fixtures.items():
                with self.subTest(label=label):
                    binary = root / f"jsonl_codex_{label}_usage_fixture.py"
                    binary.write_text(
                        "import json, sys\nsys.stdin.read()\n" + event_source,
                        encoding="utf-8",
                    )
                    result = experiment._run_codex(
                        root,
                        "synthetic prompt",
                        model="synthetic-model",
                        timeout_seconds=5,
                        command_prefix=[sys.executable, str(binary)],
                    )
                    self.assertFalse(result["usage_valid"])


if __name__ == "__main__":
    unittest.main()
