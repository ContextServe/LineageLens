"""Integration test for JavaScript & TypeScript code graph analysis in LineageLens."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lineagelens.cli import build
from lineagelens.model import SCHEMA_VERSION
from lineagelens.queries import (
    get_codebase_metrics,
    get_symbol,
    impact_analysis,
    load_graph,
    search_symbols,
)
from lineagelens.reachability import compute_reachability


class TestJsIntegration(unittest.TestCase):

    def test_js_project_analysis_integration(self):
        """Test full JS/TS project analysis, graph creation, and query engine compatibility."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            # Create package.json and TypeScript source file
            (tmp_path / "package.json").write_text('{"name": "sample-js-app"}', encoding="utf-8")

            js_src_dir = tmp_path / "src" / "controllers"
            js_src_dir.mkdir(parents=True, exist_ok=True)

            user_controller = js_src_dir / "userController.ts"
            user_controller.write_text(
                """import { Get } from "@nestjs/common";

export class UserController {

  @Get("/users")
  public async getUsers(): Promise<string[]> {
    return this.fetchUsersFromDb();
  }

  private async fetchUsersFromDb(): Promise<string[]> {
    return ["Alice", "Bob"];
  }
}
""",
                encoding="utf-8",
            )

            # Run LineageLens build on the JS/TS project
            graph_file, report_file, report = build(tmp_path, quiet=True)

            self.assertTrue(graph_file.exists())
            self.assertEqual(graph_file.name, "graph.json")

            # Verify JSON structure
            raw = json.loads(graph_file.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], SCHEMA_VERSION)
            self.assertGreater(len(raw["symbols"]), 0)


            # Load graph using LineageLens query engine
            graph = load_graph(tmp_path)
            self.assertGreaterEqual(len(graph.symbols), 3)

            # Check class symbol
            class_sym = get_symbol(graph, "src.controllers.userController.UserController")
            self.assertIsNotNone(class_sym)
            self.assertEqual(class_sym.kind, "class")

            # Check method symbol
            method_sym = get_symbol(graph, "src.controllers.userController.UserController.getUsers")
            self.assertIsNotNone(method_sym)
            self.assertEqual(method_sym.kind, "method")
            self.assertEqual(method_sym.entry_point, "api_route")

            # Check search
            results = search_symbols(graph, "getUsers")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].id, "src.controllers.userController.UserController.getUsers")

            # Check impact analysis
            impact = impact_analysis(graph, "src.controllers.userController.UserController.fetchUsersFromDb")
            self.assertEqual(impact.symbol_id, "src.controllers.userController.UserController.fetchUsersFromDb")
            self.assertGreaterEqual(len(impact.affected), 1)
            self.assertTrue(any("getUsers" in ep for ep in impact.affected_entry_points))

            # Check reachability walk
            reachability = compute_reachability(graph)
            verdict = reachability.explain("src.controllers.userController.UserController.getUsers")
            self.assertIsNotNone(verdict)
            self.assertEqual(verdict.verdict, "alive")

            metrics = get_codebase_metrics(graph)
            self.assertGreaterEqual(metrics["total_symbols"], 3)
            self.assertGreaterEqual(metrics["total_entry_points"], 1)


if __name__ == "__main__":
    unittest.main()
