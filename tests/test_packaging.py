"""The package must ship its runtime data, not just its code.

Extraction specs (``*.scm``), language manifests (``lang.toml``), contract
adapters (``*.yaml``) and the capability matrix are *data the package needs at
runtime*. A wheel containing only ``.py`` files installs an engine with nothing
to run: every query returns empty and nothing raises, which is the worst
possible failure shape.

Nothing here builds a wheel -- that needs network access for the build backend.
These check the two properties that would actually break: the data is present
relative to the installed package, and it loads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

import lineagelens

PACKAGE_ROOT = Path(lineagelens.__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]


class TestRuntimeDataIsPresent:
    def test_every_language_ships_its_spec(self):
        from lineagelens.extract import SpecRegistry

        registry = SpecRegistry()
        assert registry.languages(), "no extraction specs found next to the package"
        for lang in registry.languages():
            directory = PACKAGE_ROOT / "spec" / lang
            assert (directory / "lang.toml").is_file(), f"{lang}: manifest missing"
            assert (directory / "nodes.scm").is_file(), f"{lang}: nodes.scm missing"

    def test_contract_adapters_ship(self):
        from lineagelens.contracts import AdapterRegistry

        definitions = PACKAGE_ROOT / "contracts" / "definitions"
        assert definitions.is_dir(), "adapter definitions missing from the package"
        assert list(definitions.glob("*.yaml")), "no adapter files"
        assert len(AdapterRegistry().all_adapters()) > 20

    def test_capability_matrix_ships(self):
        """Absent, ``get_ontology`` reports every capability as untested."""
        from lineagelens.ontology import MATRIX_PATH, capability_matrix

        assert MATRIX_PATH.is_file(), (
            "matrix.json missing; regenerate with "
            "`python -m lineagelens.conformance.runner`"
        )
        assert capability_matrix()["generated"] is True

    def test_data_paths_are_package_relative(self):
        """Resolved from ``__file__``, so they survive installation.

        A path relative to the working directory works in a checkout and fails
        in a wheel -- the classic way package data goes missing.
        """
        from lineagelens.contracts.adapters import ADAPTER_ROOT
        from lineagelens.extract.spec import SPEC_ROOT
        from lineagelens.ontology import MATRIX_PATH

        for path in (SPEC_ROOT, ADAPTER_ROOT, MATRIX_PATH):
            assert path.is_absolute()
            assert PACKAGE_ROOT in path.parents or path.parent == PACKAGE_ROOT


class TestDeclaredDependencies:
    @pytest.fixture
    def pyproject(self) -> dict:
        path = REPO_ROOT / "pyproject.toml"
        if not path.is_file():
            pytest.skip("not running from a source checkout")
        return tomllib.loads(path.read_text("utf-8"))

    def test_every_grammar_is_a_core_dependency(self, pyproject):
        """Tier A is not optional, so grammars cannot sit behind an extra.

        The schema-3 build declared `tree-sitter` as an extra and *no grammar
        package at all*, so parser loading returned None for all 12 advertised
        languages and every non-Python file fell through to a regex scanner --
        every time. On Apache Dubbo that produced 4,297 "classes" matched from
        `"class " in line`.
        """
        from lineagelens.extract.langs import GRAMMARS

        declared = " ".join(pyproject["project"]["dependencies"])
        for grammar in GRAMMARS:
            assert grammar.package in declared, (
                f"{grammar.package} is registered but not a declared dependency"
            )

    def test_grammars_are_pinned_exactly(self, pyproject):
        """A `>=` floor would make `lineagelens verify` unenforceable.

        A grammar upgrade changes parse output, so it must invalidate the index
        rather than silently alter results.
        """
        loose = [
            dep for dep in pyproject["project"]["dependencies"]
            if dep.startswith("tree-sitter") and "==" not in dep
        ]
        assert not loose, f"grammars must be pinned exactly, not floored: {loose}"

    def test_no_dependency_on_deleted_subsystems(self, pyproject):
        """The GraphQL/UI stack went with the schema-3 core.

        Leaving it declared would install four packages nothing imports.
        """
        declared = " ".join(
            pyproject["project"]["dependencies"]
            + [d for deps in pyproject["project"]["optional-dependencies"].values()
               for d in deps]
        )
        for gone in ("strawberry-graphql", "fastapi", "uvicorn"):
            assert gone not in declared, f"{gone} is declared but nothing imports it"

    def test_console_scripts_resolve(self, pyproject):
        import importlib

        for target in pyproject["project"]["scripts"].values():
            module_name, _, function = target.partition(":")
            module = importlib.import_module(module_name)
            assert callable(getattr(module, function)), f"{target} is not callable"


class TestNoConfigurationRequired:
    def test_no_config_file_is_read(self):
        """There is no config file, and nothing should look for one.

        `lineagelens.yaml` configured source roots, an engine choice and a
        relation-kind allowlist. All three are gone: language is detected per
        file, there is no user-selectable engine, and every relation kind is
        always emitted.
        """
        sources = list(PACKAGE_ROOT.rglob("*.py"))
        offenders = [
            path.name for path in sources
            if "lineagelens.yaml" in path.read_text("utf-8", errors="replace")
        ]
        assert not offenders, f"these still reference a config file: {offenders}"

    def test_indexing_works_with_no_setup(self, tmp_path):
        """A bare directory of source must index with no init step."""
        (tmp_path / "m.py").write_text("def f(x):\n    return x\n")
        from lineagelens.indexer import Indexer

        store, report = Indexer(tmp_path).run()
        try:
            assert report.nodes > 0
            assert store.dangling_edge_count() == 0
        finally:
            store.close()
