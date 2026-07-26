from __future__ import annotations

import shutil
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.core.audit import audit_repository
from stateweave.core.config import load_config


class SyntheticConsumerTests(unittest.TestCase):
    def test_two_independent_repositories_use_memory_core(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        fixtures = (
            repository / "examples/research-lab",
            repository / "examples/service-team",
        )
        with TemporaryDirectory() as temporary:
            roots: list[Path] = []
            for fixture in fixtures:
                destination = Path(temporary) / fixture.name
                shutil.copytree(fixture, destination)
                roots.append(destination)

            self.assertNotEqual(roots[0], roots[1])
            for root in roots:
                with self.subTest(consumer=root.name):
                    config = load_config(root)
                    report = audit_repository(config, today=date(2026, 7, 25))
                    self.assertTrue(report.ok, report.errors)
                    self.assertEqual(report.record_count, 3)
                    self.assertTrue(report.backlinks)


if __name__ == "__main__":
    unittest.main()
