from __future__ import annotations

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


class CodexValueExperimentTests(unittest.TestCase):
    def test_codex_cli_version_evidence_is_sanitized(self) -> None:
        with TemporaryDirectory() as temporary:
            binary = Path(temporary) / "codex_version_fixture.py"
            binary.write_text(
                "import sys\n"
                "print('codex-cli 9.8.7')\n"
                "print('DO_NOT_PERSIST_VERSION_STDERR', file=sys.stderr)\n",
                encoding="utf-8",
            )

            observed = experiment._codex_cli_observation([sys.executable, str(binary)])

            self.assertEqual(
                observed,
                {
                    "observed": True,
                    "implementation": "codex-cli",
                    "version": "9.8.7",
                    "version_output_sha256": experiment.sha256_bytes(
                        b"codex-cli 9.8.7\n"
                    ),
                },
            )
            encoded = json.dumps(observed)
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

    def test_context_arms_are_bounded_and_projection_preserves_bindings(self) -> None:
        records = experiment._memory_records("shape")
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
                    "reasons": ["term:shape:body"],
                    "content": {
                        "title": "Shape",
                        "statement": "Reject mismatch.",
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
    def test_fake_executor_repairs_all_three_seeded_defects(self) -> None:
        with TemporaryDirectory() as temporary:
            source = source_project(Path(temporary) / "source")
            for task in experiment.TASKS:
                workspace = Path(temporary) / task
                workspace.mkdir()
                experiment._prepare_workspace(source, workspace, task)
                before = experiment._tree_snapshot(workspace)
                experiment._apply_fake_success(workspace, task)
                tests = experiment._run_tests(workspace)
                changed = experiment._changed_paths(
                    before,
                    experiment._tree_snapshot(workspace),
                )
                self.assertTrue(tests["passed"], task)
                self.assertEqual(changed, [experiment.TARGET_MODULE.as_posix()])

    @unittest.skipUnless(
        NUMPY_AVAILABLE,
        "the optional target-project NumPy dependency is unavailable",
    )
    def test_dry_run_closes_bridge_without_persisting_content(self) -> None:
        with TemporaryDirectory() as temporary:
            source = source_project(Path(temporary) / "source")
            run = experiment._one_run(
                source,
                task_name="shape",
                arm="bundle",
                repetition=1,
                execute=False,
                fake_failure=False,
                model="unused",
                timeout_seconds=30,
            )

            self.assertTrue(run["success"])
            self.assertTrue(all(run["audits"].values()))
            self.assertIsNone(run["monetary_cost"])
            encoded = json.dumps(run)
            self.assertNotIn("<STATEWEAVE_CONTEXT>", encoded)
            self.assertNotIn("different shapes using ValueError", encoded)

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
                patch.object(
                    experiment,
                    "run_experiment",
                ) as mocked,
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
            self.assertEqual(result["event_types"], {"turn.completed": 1})
            self.assertEqual(result["discarded_event_count"], 1)
            self.assertNotIn("DO_NOT_PERSIST", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
