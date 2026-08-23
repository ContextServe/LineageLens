"""GraphIndex: cached adjacency views, and the inference-call budget.

Two separate problems are covered here. First, every REST endpoint used to
re-read and re-parse graph.json per request, and the metrics pass ran a
transitive walk per symbol. Second, the inference engine was consulted at every
call site, which made analysis 3x slower for no gain.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import lineagelens.resolve_jedi as resolve_jedi
from lineagelens.analyzer import analyze
from lineagelens.cli import write_artifacts
from lineagelens.config import ProjectConfig
from lineagelens.index import index_for, invalidate, load_index


def make_project(root: Path) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text(
        "def a():\n    return b()\n"
        "\n"
        "def b():\n    return c()\n"
        "\n"
        "def c():\n    return 1\n"
        "\n"
        "def loops():\n    return loops()\n"
        "\n"
        "def ping():\n    return pong()\n"
        "\n"
        "def pong():\n    return ping()\n",
        encoding="utf-8",
    )
    return root


# ------------------------------------------------------------------- caching
def test_index_is_reused_while_the_graph_is_unchanged(tmp_path):
    root = make_project(tmp_path)
    config = ProjectConfig.load(root)
    graph, report = analyze(root, config)
    write_artifacts(root, config, graph, report, quiet=True)
    invalidate(root)

    first = load_index(root)
    second = load_index(root)
    assert first is second, "a second load re-parsed the graph instead of reusing it"


def test_index_is_rebuilt_when_the_graph_changes(tmp_path):
    root = make_project(tmp_path)
    config = ProjectConfig.load(root)
    graph, report = analyze(root, config)
    _graph_file, _ = write_artifacts(root, config, graph, report, quiet=True)
    invalidate(root)

    first = load_index(root)
    (root / "src" / "pkg" / "extra.py").write_text("def d():\n    return 1\n")
    graph, report = analyze(root, config)
    write_artifacts(root, config, graph, report, quiet=True)

    second = load_index(root)
    assert second is not first, "a changed graph.json was served from the stale cache"
    assert "pkg.extra.d" in second.graph.symbols


def test_invalidate_drops_the_cache(tmp_path):
    root = make_project(tmp_path)
    config = ProjectConfig.load(root)
    graph, report = analyze(root, config)
    write_artifacts(root, config, graph, report, quiet=True)

    first = load_index(root)
    invalidate(root)
    assert load_index(root) is not first


# ----------------------------------------------------------------- adjacency
def test_adjacency_matches_a_linear_scan(tmp_path):
    root = make_project(tmp_path)
    graph, _ = analyze(root)
    index = index_for(graph)

    for symbol_id in graph.symbols:
        assert index.callers_of(symbol_id) == [
            r for r in graph.relations if r.target == symbol_id
        ]
        assert index.callees_of(symbol_id) == [
            r for r in graph.relations if r.source == symbol_id
        ]


def test_call_depth_is_a_chain_length_not_a_reachable_set_size(tmp_path):
    """a -> b -> c is depth 2 from a, not "3 symbols reachable"."""
    root = make_project(tmp_path)
    graph, _ = analyze(root)
    chains = index_for(graph).max_call_chain

    assert chains["pkg.mod.a"] == 2
    assert chains["pkg.mod.b"] == 1
    assert chains["pkg.mod.c"] == 0


def test_call_depth_terminates_on_recursion(tmp_path):
    """Recursion must terminate and give the same answer regardless of walk order.

    A self-recursive function is a single cyclic component of size 1, so it adds
    no chain length: there is no path through it that visits a second distinct
    symbol.
    """
    root = make_project(tmp_path)
    graph, _ = analyze(root)
    chains = index_for(graph).max_call_chain
    assert chains["pkg.mod.loops"] == 0


def test_call_depth_terminates_on_mutual_recursion(tmp_path):
    root = make_project(tmp_path)
    graph, _ = analyze(root)
    chains = index_for(graph).max_call_chain
    # One SCC of size 2, so both members report the same chain length: 1.
    # The old memoised DFS gave ping=2 and pong=1 purely from walk order.
    assert chains["pkg.mod.ping"] == 1
    assert chains["pkg.mod.pong"] == 1


def test_duplicate_names_index_matches_the_query(tmp_path):
    from lineagelens.queries import find_duplicate_names

    root = tmp_path
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "a.py").write_text("def dup():\n    return 1\n")
    (root / "src" / "pkg" / "b.py").write_text("def dup():\n    return 2\n")
    graph, _ = analyze(root)

    assert index_for(graph).duplicate_names == find_duplicate_names(graph)
    assert ("function", "dup") in index_for(graph).duplicate_names


# ------------------------------------------------------- inference gating
def test_inference_is_not_consulted_for_third_party_calls(tmp_path, monkeypatch):
    """The engine is skipped when no in-repo symbol could possibly match.

    A symbol's last dotted component always equals its name, so if the call's
    trailing name is not a known symbol name the engine cannot help.
    """
    root = tmp_path
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text(
        "import json\n"
        "import os\n"
        "\n"
        "def go(items):\n"
        "    for item in items:\n"
        "        print(json.dumps(item))\n"
        "        os.path.join('a', 'b')\n"
        "        len(item)\n"
        "        item.append(1)\n"
        "    return items\n",
        encoding="utf-8",
    )

    calls = []
    original = resolve_jedi.JediResolver.resolve_call

    def counted(self, *args, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(resolve_jedi.JediResolver, "resolve_call", counted)
    analyze(root)
    assert calls == [], f"engine consulted {len(calls)} times for third-party calls only"


def test_inference_is_still_consulted_when_it_could_help(tmp_path, monkeypatch):
    root = tmp_path
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text(
        "class Thing:\n"
        "    def act(self):\n"
        "        return 1\n"
        "\n"
        "def go(box):\n"
        "    return box.act()\n",
        encoding="utf-8",
    )

    calls = []
    original = resolve_jedi.JediResolver.resolve_call

    def counted(self, *args, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(resolve_jedi.JediResolver, "resolve_call", counted)
    analyze(root)
    assert calls, "`box.act()` names an in-repo method; the engine must be tried"


def test_disabling_inference_still_produces_a_complete_graph(tmp_path):
    root = make_project(tmp_path)
    base = ProjectConfig.load(root)
    off = replace(base, analysis=replace(base.analysis, jedi=False))

    with_engine, _ = analyze(root, base)
    without, report = analyze(root, off)

    assert set(without.symbols) == set(with_engine.symbols)
    assert len(without.relations) == len(with_engine.relations)
    assert not report.failures
    assert not any(r.resolution_evidence.label == "jedi_inference" for r in without.relations)


def test_inference_budget_is_reported_when_exhausted(tmp_path):
    root = tmp_path
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text(
        "class Thing:\n"
        "    def act(self):\n"
        "        return 1\n"
        "\n"
        "def go(a, b, c):\n"
        "    return a.act() + b.act() + c.act()\n",
        encoding="utf-8",
    )
    base = ProjectConfig.load(root)
    capped = replace(base, analysis=replace(base.analysis, jedi_max_calls=1))

    _, report = analyze(root, capped)
    assert any("budget" in w.message for w in report.warnings), (
        "exhausting the budget must be visible in the report, not only in logs"
    )


def test_call_depth_is_order_independent(tmp_path):
    """The same graph must report the same depths whatever order symbols are in.

    A memoised cycle-cutting DFS caches a value computed while an ancestor was on
    the stack, so a node inside a cycle got a different answer depending on which
    member the walk reached first.
    """
    root = tmp_path
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text(
        "def a():\n    return b()\n"
        "\n"
        "def b():\n    return c()\n"
        "\n"
        "def c():\n    return a()\n"
        "\n"
        "def entry():\n    return a()\n",
        encoding="utf-8",
    )
    graph, _ = analyze(root)
    baseline = index_for(graph).max_call_chain

    for rotation in range(1, 4):
        ids = list(graph.symbols)
        rotated = ids[rotation:] + ids[:rotation]
        shuffled = type(graph)(project_root=graph.project_root)
        for sid in rotated:
            shuffled.add_symbol(graph.symbols[sid])
        for container in graph.containers.values():
            shuffled.add_container(container)
        for relation in graph.relations:
            shuffled.add_relation(relation)
        assert index_for(shuffled).max_call_chain == baseline, (
            f"depths changed when symbol order was rotated by {rotation}"
        )

    # a/b/c form one cycle (chain 2 within the component); entry adds one hop.
    assert baseline["pkg.mod.a"] == 2
    assert baseline["pkg.mod.b"] == 2
    assert baseline["pkg.mod.c"] == 2
    assert baseline["pkg.mod.entry"] == 3
