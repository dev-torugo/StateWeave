from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.core.errors import LockUnavailableError
from stateweave.core.locking import WriterLock


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


if __name__ == "__main__":
    unittest.main()
