"""Unit tests for LineageLensWatcher and DebouncedEventHandler."""

import time
import tempfile
from pathlib import Path
import unittest

from lineagelens.watcher import LineageLensWatcher


class TestLineageLensWatcher(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        # Create minimal config
        config_file = self.project_root / "lineagelens.yaml"
        config_file.write_text("source_roots: ['.']\n")
        self.watcher = LineageLensWatcher(self.project_root)

    def tearDown(self):
        self.watcher.stop()
        self.temp_dir.cleanup()

    def test_watcher_status_and_pid(self):
        status = self.watcher.get_status()
        self.assertFalse(status["running"])

        self.watcher.start(daemon=True)
        time.sleep(0.5)

        status_running = self.watcher.get_status()
        self.assertTrue(status_running["running"])
        self.assertIsNotNone(status_running["pid"])

        stopped = self.watcher.stop()
        self.assertTrue(stopped)
        
        status_stopped = self.watcher.get_status()
        self.assertFalse(status_stopped["running"])

    def test_watcher_detects_file_save_and_syncs_sqlite(self):
        self.watcher.start(daemon=True)
        time.sleep(0.5)

        test_file = self.project_root / "sample.py"
        test_file.write_text("class TestWatcher:\n    def run(self): pass\n")

        # Wait for debounce and sync
        time.sleep(1.2)

        sym = self.watcher.db.get_symbol("sample.py::TestWatcher")
        self.assertIsNotNone(sym)
        self.assertEqual(sym.name, "TestWatcher")


if __name__ == "__main__":
    unittest.main()
