from __future__ import annotations

import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.core.errors import LockUnavailableError
from stateweave.core.locking import (
    MISSING_OWNER_FINGERPRINT,
    WriterLock,
    inspect_writer_lock,
    recover_stale_writer_lock,
)
from stateweave.cli import main

from tests.helpers import project


class WriterLockTests(unittest.TestCase):
    def test_lock_is_exclusive_and_reusable_after_release(self) -> None:
        with TemporaryDirectory() as temporary:
            metadata = Path(temporary)
            first = WriterLock(
                metadata,
                timeout_seconds=0.05,
                stale_after_seconds=60,
                poll_interval=0.005,
            )
            second = WriterLock(
                metadata,
                timeout_seconds=0.02,
                stale_after_seconds=60,
                poll_interval=0.005,
            )
            with first:
                with self.assertRaises(LockUnavailableError) as captured:
                    second.acquire()
                self.assertFalse(captured.exception.stale)
            with second:
                self.assertTrue(second.acquired)
            self.assertFalse((metadata / "writer.lock").exists())

    def test_stale_lock_is_reported_but_not_stolen(self) -> None:
        with TemporaryDirectory() as temporary:
            metadata = Path(temporary)
            lock_dir = metadata / "writer.lock"
            lock_dir.mkdir()
            (lock_dir / "owner.json").write_text(
                json.dumps(
                    {
                        "token": "other",
                        "acquired_at": "2000-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            candidate = WriterLock(
                metadata,
                timeout_seconds=0.01,
                stale_after_seconds=1,
                poll_interval=0.002,
            )
            with self.assertRaises(LockUnavailableError) as captured:
                candidate.acquire()
            self.assertTrue(captured.exception.stale)
            self.assertTrue(lock_dir.exists())
            self.assertEqual(
                json.loads((lock_dir / "owner.json").read_text())["token"],
                "other",
            )

    def test_wrong_owner_token_blocks_release(self) -> None:
        with TemporaryDirectory() as temporary:
            metadata = Path(temporary)
            lock = WriterLock(
                metadata,
                timeout_seconds=0.05,
                stale_after_seconds=60,
            ).acquire()
            owner = json.loads(lock.owner_file.read_text())
            owner["token"] = "replaced"
            lock.owner_file.write_text(json.dumps(owner), encoding="utf-8")
            with self.assertRaises(LockUnavailableError):
                lock.release()
            self.assertTrue(lock.lock_dir.exists())

    def test_lock_inspection_is_read_only_and_fingerprint_bound(self) -> None:
        with TemporaryDirectory() as temporary:
            metadata = Path(temporary)
            lock = WriterLock(
                metadata,
                timeout_seconds=0.05,
                stale_after_seconds=60,
            )
            with lock:
                inspection = inspect_writer_lock(
                    metadata,
                    stale_after_seconds=60,
                )
                self.assertTrue(inspection.exists)
                self.assertFalse(inspection.stale)
                self.assertTrue(inspection.safe_to_recover)
                self.assertEqual(inspection.owner_token, lock.token)
                self.assertIsNotNone(inspection.owner_sha256)
                self.assertTrue(lock.lock_dir.exists())

    def test_stale_lock_recovery_requires_exact_fingerprint_and_token(self) -> None:
        with TemporaryDirectory() as temporary:
            metadata = Path(temporary)
            lock_dir = metadata / "writer.lock"
            lock_dir.mkdir()
            owner = {
                "schema_version": 1,
                "token": "explicit-owner",
                "pid": 123,
                "hostname": "synthetic.invalid",
                "acquired_at": "2000-01-01T00:00:00Z",
            }
            (lock_dir / "owner.json").write_text(
                json.dumps(owner),
                encoding="utf-8",
            )
            inspection = inspect_writer_lock(metadata, stale_after_seconds=1)
            assert inspection.owner_sha256 is not None

            with self.assertRaises(LockUnavailableError):
                recover_stale_writer_lock(
                    metadata,
                    stale_after_seconds=1,
                    expected_owner_sha256="0" * 64,
                    expected_token="explicit-owner",
                )
            self.assertTrue(lock_dir.exists())

            recovered = recover_stale_writer_lock(
                metadata,
                stale_after_seconds=1,
                expected_owner_sha256=inspection.owner_sha256,
                expected_token="explicit-owner",
            )
            self.assertEqual(recovered.owner_token, "explicit-owner")
            self.assertFalse(lock_dir.exists())

    def test_missing_owner_lock_can_only_be_recovered_after_stale_threshold(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            metadata = Path(temporary)
            lock_dir = metadata / "writer.lock"
            lock_dir.mkdir()
            fresh = inspect_writer_lock(metadata, stale_after_seconds=60)
            self.assertFalse(fresh.stale)
            self.assertEqual(fresh.owner_sha256, MISSING_OWNER_FINGERPRINT)
            with self.assertRaises(LockUnavailableError):
                recover_stale_writer_lock(
                    metadata,
                    stale_after_seconds=60,
                    expected_owner_sha256=MISSING_OWNER_FINGERPRINT,
                    expected_token=None,
                )

            os.utime(lock_dir, (1, 1))
            stale = inspect_writer_lock(metadata, stale_after_seconds=60)
            self.assertTrue(stale.stale)
            recover_stale_writer_lock(
                metadata,
                stale_after_seconds=60,
                expected_owner_sha256=MISSING_OWNER_FINGERPRINT,
                expected_token=None,
            )
            self.assertFalse(lock_dir.exists())

    def test_cli_requires_status_evidence_and_explicit_confirmation(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            lock_dir = config.metadata_dir / "writer.lock"
            lock_dir.mkdir()
            (lock_dir / "owner.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "token": "cli-owner",
                        "pid": 123,
                        "hostname": "synthetic.invalid",
                        "acquired_at": "2000-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                status = main(["lock-status", "--config", str(config.root)])
            self.assertEqual(status, 0)
            inspection = json.loads(output.getvalue())

            error = StringIO()
            with redirect_stderr(error):
                refused = main(
                    [
                        "recover-lock",
                        "--config",
                        str(config.root),
                        "--owner-sha256",
                        inspection["owner_sha256"],
                        "--token",
                        "cli-owner",
                    ]
                )
            self.assertEqual(refused, 2)
            self.assertIn("--confirm-stale", error.getvalue())
            self.assertTrue(lock_dir.exists())

            output = StringIO()
            with redirect_stdout(output):
                recovered = main(
                    [
                        "recover-lock",
                        "--config",
                        str(config.root),
                        "--owner-sha256",
                        inspection["owner_sha256"],
                        "--token",
                        "cli-owner",
                        "--confirm-stale",
                    ]
                )
            self.assertEqual(recovered, 0)
            self.assertTrue(json.loads(output.getvalue())["recovered"])
            self.assertFalse(lock_dir.exists())


if __name__ == "__main__":
    unittest.main()
