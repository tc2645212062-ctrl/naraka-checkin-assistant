import sys
import tempfile
import unittest
from pathlib import Path
from assistant.platform import InstanceLock


@unittest.skipUnless(sys.platform == "win32", "Windows file lock")
class LockTests(unittest.TestCase):
    def test_second_instance_is_rejected_and_can_retry_after_release(self):
        with tempfile.TemporaryDirectory() as folder:
            first = InstanceLock(Path(folder)/"instance.lock")
            second = InstanceLock(first.path)
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                first.close()
                self.assertTrue(second.acquire())
            finally:
                first.close()
                second.close()
