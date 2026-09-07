"""Unit tests for IncrementalAnalyzer."""

import tempfile
from pathlib import Path
import unittest

from lineagelens.db import SQLiteIndexDB
from lineagelens.incremental import IncrementalAnalyzer


class TestIncrementalAnalyzer(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        self.db_path = self.project_root / ".lineagelens" / "index.sqlite"
        self.db = SQLiteIndexDB(self.db_path)
        self.analyzer = IncrementalAnalyzer()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_file_new_and_updated_python_file(self):
        rel_file = "src/example.py"
        abs_file = self.project_root / rel_file
        abs_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 1. Initial write
        abs_file.write_text("class Foo:\n    def bar(self):\n        pass\n")

        success = self.analyzer.sync_file(self.project_root, rel_file, self.db)
        self.assertTrue(success)

        sym = self.db.get_symbol("src/example.py::Foo")
        self.assertIsNotNone(sym)
        self.assertEqual(sym.name, "Foo")

        method_sym = self.db.get_symbol("src/example.py::Foo.bar")
        self.assertIsNotNone(method_sym)
        self.assertEqual(method_sym.name, "bar")

        # 2. Modify file content (add new method)
        abs_file.write_text("class Foo:\n    def bar(self):\n        pass\n    def baz(self):\n        pass\n")
        
        success2 = self.analyzer.sync_file(self.project_root, rel_file, self.db)
        self.assertTrue(success2)

        baz_sym = self.db.get_symbol("src/example.py::Foo.baz")
        self.assertIsNotNone(baz_sym)
        self.assertEqual(baz_sym.name, "baz")

    def test_sync_file_deletion_purges_index(self):
        rel_file = "src/temp.py"
        abs_file = self.project_root / rel_file
        abs_file.parent.mkdir(parents=True, exist_ok=True)
        abs_file.write_text("def temporary_function(): pass\n")

        self.analyzer.sync_file(self.project_root, rel_file, self.db)
        self.assertIsNotNone(self.db.get_symbol("src/temp.py::temporary_function"))

        # Delete file
        abs_file.unlink()

        success = self.analyzer.sync_file(self.project_root, rel_file, self.db)
        self.assertTrue(success)
        self.assertIsNone(self.db.get_symbol("src/temp.py::temporary_function"))


if __name__ == "__main__":
    unittest.main()
