from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class BenchmarkScriptTests(unittest.TestCase):
    def test_small_benchmark_proves_scan_index_equivalence(self) -> None:
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
                "2",
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
        result = payload["results"][0]
        self.assertEqual(result["records"], 11)
        self.assertEqual(result["selected_items"], 8)
        self.assertGreater(result["context_scan"]["p95_ms"], 0)
        self.assertGreater(result["context_indexed"]["p95_ms"], 0)


if __name__ == "__main__":
    unittest.main()
