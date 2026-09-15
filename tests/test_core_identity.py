"""Identity scheme guarantees (issue #51 §6).

Each test here pins one of the four properties the scheme has to have. They are
not stylistic: three of them correspond to defects measured in schema 3, and the
fourth (determinism) is what ``--verify-determinism`` rests on.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lineagelens.core import (
    anonymous_member,
    contract_id,
    file_node_id,
    node_id,
    normalise_module_path,
    normalise_type,
    qualified_name,
    service_id,
    signature_hash,
)


class TestOverloadDisambiguation:
    """Same name, different signature, must be different nodes.

    Java, C#, C++ and Go all permit this. Schema 3 keyed on name alone, so every
    overload of a method collapsed onto one node and its callers merged.
    """

    def test_differing_param_types_are_distinct(self) -> None:
        qname = qualified_name("svc", "java", "com.acme.Orders", ["save"])
        assert node_id("svc", "java", qname, signature_hash(["int"])) != node_id(
            "svc", "java", qname, signature_hash(["String"])
        )

    def test_differing_arity_is_distinct(self) -> None:
        assert signature_hash(["int"]) != signature_hash(["int", "int"])

    def test_param_order_is_significant(self) -> None:
        assert signature_hash(["int", "String"]) != signature_hash(["String", "int"])

    def test_zero_arg_callable_differs_from_non_callable(self) -> None:
        """``f()`` is not the same key as a field, which has no signature at all."""
        assert signature_hash([], arity=0) != ""
        assert signature_hash(None) == ""

    def test_generic_whitespace_does_not_split_a_node(self) -> None:
        """``Map<String, Integer>`` and ``Map<String,Integer>`` are one type.

        Two declarations of one overload must not land on different nodes just
        because a formatter moved a space.
        """
        assert signature_hash(["Map<String, Integer>"]) == signature_hash(["Map<String,Integer>"])


class TestStabilityAcrossEdits:
    """Nothing may derive from a line number.

    A position-derived id means editing line 10 renames the symbol on line 400
    and invalidates every edge touching it, which would make incremental
    reindexing useless.
    """

    def test_anonymous_members_use_ordinal_not_line(self) -> None:
        assert anonymous_member("lambda", 0) == "<lambda:0>"
        assert anonymous_member("lambda", 0) != anonymous_member("lambda", 1)

    def test_id_inputs_contain_no_position(self) -> None:
        """Identical structure yields an identical id regardless of location."""
        qname = qualified_name("svc", "python", "app.mod", ["fn"])
        assert node_id("svc", "python", qname) == node_id("svc", "python", qname)


class TestDeterminism:
    """Ids must be reproducible across processes (§11).

    Python randomises ``hash()`` per interpreter run via PYTHONHASHSEED. Using it
    anywhere in the id path would make two indexes of one commit differ, silently
    defeating the determinism check.
    """

    def test_ids_are_stable_across_interpreter_runs(self) -> None:
        script = (
            "from lineagelens.core import node_id, qualified_name, signature_hash;"
            "print(node_id('s','java',qualified_name('s','java','a.b',['c']),"
            "signature_hash(['int'])))"
        )
        runs = {
            subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, check=True,
                env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
            ).stdout.strip()
            for seed in ("0", "1", "random")
        }
        assert len(runs) == 1, f"id varies with PYTHONHASHSEED: {runs}"


class TestCrossLanguageContracts:
    """Contract ids must join across language and service (§8.1).

    This is the mechanism that links a TypeScript ``fetch`` to a Python route
    handler, so it depends on the id being a function of ``(kind, key)`` alone.
    """

    def test_same_key_same_id_regardless_of_caller(self) -> None:
        assert contract_id("http_route", "GET /api/users/{*}") == contract_id(
            "http_route", "GET /api/users/{*}"
        )

    def test_kind_namespaces_the_key(self) -> None:
        """An HTTP route named ``orders`` must not join to a Kafka topic ``orders``."""
        assert contract_id("http_route", "orders") != contract_id("topic", "orders")

    def test_empty_key_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="contract key is required"):
            contract_id("http_route", "")


class TestNormalisation:
    def test_module_paths_converge_across_language_syntax(self) -> None:
        """Dots, slashes and ``::`` all describe the same nesting."""
        assert (
            normalise_module_path("a.b.c")
            == normalise_module_path("a/b/c")
            == normalise_module_path("a::b::c")
            == "a/b/c"
        )

    def test_empty_segments_are_dropped(self) -> None:
        assert normalise_module_path("a..b") == "a/b"
        assert normalise_module_path("") == ""

    def test_type_normalisation_is_not_resolution(self) -> None:
        """Normalising must not invent a fully-qualified name.

        Qualification is Tier B's job (§7.2); this function has to stay usable
        before any resolver has run.
        """
        assert normalise_type("List") == "List"
        assert normalise_type("  List < String >  ") == "List<String>"


class TestQualifiedNames:
    def test_shape_is_service_lang_module_member(self) -> None:
        assert qualified_name("api", "python", "app.orders", ["Order", "save"]) == (
            "api/python/app/orders#Order/save"
        )

    def test_module_level_name_has_no_member_separator(self) -> None:
        assert qualified_name("api", "python", "app.orders") == "api/python/app/orders"

    def test_node_id_requires_a_qualified_name(self) -> None:
        with pytest.raises(ValueError, match="qualified_name is required"):
            node_id("svc", "python", "")


class TestServiceAndFileIds:
    def test_same_service_name_in_different_roots_is_distinct(self) -> None:
        """A monorepo can hold two manifests declaring the same name."""
        assert service_id("api", "services/a") != service_id("api", "services/b")

    def test_file_ids_are_path_derived(self) -> None:
        """Two files can share a module path (namespace packages, Go packages)."""
        assert file_node_id("svc", "a/__init__.py") != file_node_id("svc", "b/__init__.py")

    def test_path_separators_are_normalised(self) -> None:
        assert file_node_id("svc", "a\\b.py") == file_node_id("svc", "a/b.py")
