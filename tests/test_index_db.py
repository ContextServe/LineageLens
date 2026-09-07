"""Unit tests for SQLiteIndexDB storage engine."""

import tempfile
from pathlib import Path
import unittest

from lineagelens.db import SQLiteIndexDB
from lineagelens.model import CodeGraph, Evidence, Relation, Symbol


class TestSQLiteIndexDB(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / ".lineagelens" / "index.sqlite"
        self.db = SQLiteIndexDB(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_db_creates_tables_and_indexes(self):
        self.assertTrue(self.db_path.exists())
        with self.db.get_connection() as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            self.assertIn("files", tables)
            self.assertIn("symbols", tables)
            self.assertIn("containers", tables)
            self.assertIn("relations", tables)
            self.assertIn("reachability_cache", tables)

    def test_upsert_file_graph_and_queries(self):
        rel_file = "src/foo.py"
        sym1 = Symbol(
            id="src/foo.py::Bar",
            name="Bar",
            kind="class",
            file=rel_file,
            line=1,
            end_line=10,
            module="src.foo",
        )
        sym2 = Symbol(
            id="src/foo.py::Bar.baz",
            name="baz",
            kind="method",
            file=rel_file,
            line=5,
            end_line=9,
            module="src.foo",
            parent="src/foo.py::Bar",
        )
        rel1 = Relation(
            source="src/foo.py::Bar.baz",
            target="src/helper.py::run",
            kind="CALLS",
            file=rel_file,
            line=7,
            evidence=Evidence(tier="deterministic_fact", label="static_ast"),
            resolution="resolved",
        )

        self.db.upsert_file_graph(
            rel_file_path=rel_file,
            lang="python",
            mtime=12345.67,
            file_hash="hash123",
            symbols=[sym1, sym2],
            relations=[rel1],
        )

        fetched_sym = self.db.get_symbol("src/foo.py::Bar.baz")
        self.assertIsNotNone(fetched_sym)
        self.assertEqual(fetched_sym.name, "baz")
        self.assertEqual(fetched_sym.file, rel_file)

        search_res = self.db.search_symbols(query="baz")
        self.assertEqual(len(search_res), 1)
        self.assertEqual(search_res[0].id, "src/foo.py::Bar.baz")

        callees = self.db.get_callees("src/foo.py::Bar.baz")
        self.assertEqual(len(callees), 1)
        self.assertEqual(callees[0].target, "src/helper.py::run")

        callers = self.db.get_callers("src/helper.py::run")
        self.assertEqual(len(callers), 1)
        self.assertEqual(callers[0].source, "src/foo.py::Bar.baz")

    def test_purge_file_cascade(self):
        rel_file = "src/bar.py"
        sym1 = Symbol(
            id="src/bar.py::fn",
            name="fn",
            kind="function",
            file=rel_file,
            line=1,
            module="src.bar",
        )
        rel1 = Relation(
            source="src/bar.py::fn",
            target="src/ext.py::target",
            kind="CALLS",
            file=rel_file,
            line=2,
        )

        self.db.upsert_file_graph(
            rel_file_path=rel_file,
            lang="python",
            mtime=100.0,
            file_hash="h1",
            symbols=[sym1],
            relations=[rel1],
        )

        self.assertIsNotNone(self.db.get_symbol("src/bar.py::fn"))
        self.db.purge_file(rel_file)
        self.assertIsNone(self.db.get_symbol("src/bar.py::fn"))

        callees = self.db.get_callees("src/bar.py::fn")
        self.assertEqual(len(callees), 0)

    def test_save_and_to_code_graph_roundtrip(self):
        graph = CodeGraph(project_root="/tmp/project")
        sym = Symbol(
            id="m.py::func",
            name="func",
            kind="function",
            file="m.py",
            line=10,
            module="m",
        )
        rel = Relation(
            source="m.py::func",
            target="other.py::target",
            kind="CALLS",
            file="m.py",
            line=12,
        )
        graph.add_symbol(sym)
        graph.add_relation(rel)

        self.db.save_code_graph(graph)

        loaded_graph = self.db.to_code_graph(project_root="/tmp/project")
        self.assertIn("m.py::func", loaded_graph.symbols)
        self.assertEqual(len(loaded_graph.relations), 1)
        self.assertEqual(loaded_graph.relations[0].target, "other.py::target")


if __name__ == "__main__":
    unittest.main()
