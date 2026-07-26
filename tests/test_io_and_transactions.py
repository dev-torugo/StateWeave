from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.core.audit import audit_repository
from stateweave.core.errors import PathBoundaryError, RecordError
from stateweave.core.io import read_json, safe_relative_path
from stateweave.core.project import put_records

from tests.helpers import fact, project, write_fact


class IoAndTransactionTests(unittest.TestCase):
    def test_non_finite_json_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "record.json"
            path.write_text('{"value": NaN}', encoding="utf-8")
            with self.assertRaises(RecordError):
                read_json(path, max_bytes=1024)

    def test_portable_relative_path_rejects_ambiguous_forms(self) -> None:
        for value in (
            "../escape",
            "..\\escape",
            "C:\\escape",
            "file:stream",
            "/absolute",
            "",
        ):
            with self.subTest(value=value):
                with self.assertRaises(PathBoundaryError):
                    safe_relative_path(value)
        self.assertEqual(
            safe_relative_path("memory/facts/FCT-one.json").as_posix(),
            "memory/facts/FCT-one.json",
        )

    def test_reciprocal_supersession_can_be_written_as_one_batch(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            write_fact(config, fact("FCT-old", value="old"))
            old = fact(
                "FCT-old",
                status="deprecated",
                superseded_by="FCT-new",
                value="old",
            )
            new = fact(
                "FCT-new",
                supersedes=["FCT-old"],
                value="new",
            )

            put_records(config, [old, new], overwrite=True)

            report = audit_repository(config)
            self.assertTrue(report.ok, report.errors)

    def test_invalid_batch_rolls_back_every_record(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            original = fact("FCT-old", value="old")
            path = write_fact(config, original)
            old = fact(
                "FCT-old",
                status="deprecated",
                superseded_by="FCT-missing",
                value="old",
            )
            with self.assertRaises(RecordError):
                put_records(config, [old], overwrite=True)
            self.assertEqual(
                read_json(path, max_bytes=config.limits.max_record_bytes),
                original,
            )


if __name__ == "__main__":
    unittest.main()
