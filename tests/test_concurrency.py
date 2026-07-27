from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.core.audit import audit_repository
from stateweave.core.transactions import transaction_id_for_key

from tests.helpers import project

WRITER_SCRIPT = """
import sys
from stateweave.core.config import load_config
from stateweave.core.project import put_record
from tests.helpers import fact

config = load_config(sys.argv[1])
put_record(
    config,
    fact(sys.argv[2], value=sys.argv[3]),
    idempotency_key=sys.argv[4],
)
"""


class ConcurrentAgentTests(unittest.TestCase):
    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        source_root = str(Path(__file__).resolve().parents[1] / "src")
        environment["PYTHONPATH"] = os.pathsep.join(
            value for value in (source_root, environment.get("PYTHONPATH", "")) if value
        )
        return environment

    def _writers(
        self,
        root: Path,
        requests: list[tuple[str, str, str]],
    ) -> list[subprocess.Popen[str]]:
        repository = Path(__file__).resolve().parents[1]
        return [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    WRITER_SCRIPT,
                    str(root),
                    identifier,
                    value,
                    key,
                ],
                cwd=repository,
                env=self._environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for identifier, value, key in requests
        ]

    def _assert_success(self, processes: list[subprocess.Popen[str]]) -> None:
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(
                process.returncode,
                0,
                f"stdout={stdout!r}; stderr={stderr!r}",
            )

    def test_independent_agents_serialize_without_lost_updates(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            requests = [
                (f"FCT-concurrent-{index}", "shared", f"agent-{index}")
                for index in range(6)
            ]

            self._assert_success(self._writers(config.root, requests))

            self.assertTrue(audit_repository(config).ok)
            self.assertEqual(len(list(config.facts_dir.glob("*.json"))), 6)
            transaction_files = list(
                (config.metadata_dir / "transactions").glob("IDEM-*.json")
            )
            self.assertEqual(len(transaction_files), 6)

    def test_agents_replaying_same_operation_converge_on_one_receipt(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            requests = [
                ("FCT-concurrent-replay", "stable", "shared-operation")
                for _ in range(5)
            ]

            self._assert_success(self._writers(config.root, requests))

            self.assertTrue(audit_repository(config).ok)
            self.assertEqual(len(list(config.facts_dir.glob("*.json"))), 1)
            transaction_id, _ = transaction_id_for_key("shared-operation")
            journal = config.metadata_dir / "transactions" / f"{transaction_id}.json"
            self.assertTrue(journal.is_file())


if __name__ == "__main__":
    unittest.main()
