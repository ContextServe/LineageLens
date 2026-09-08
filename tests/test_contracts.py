"""Cross-framework and cross-service contracts (issue #51 §8).

Two things are being pinned here.

**The join.** A TypeScript ``fetch('/api/users/42')`` and a Python
``@router.get('/api/users/{id}')`` must land on one contract node. If key
normalisation splits them, the contract silently becomes two nodes -- the
``EXPOSES`` side on one and the ``CONSUMES`` side on the other -- and the
cross-service link just does not appear. That failure is invisible without a
test, which is why §8.3 makes the normaliser its own component.

**The honesty of a missing adapter.** Adapters are data, so coverage depends on
someone having written a YAML file for a given framework. Left alone that would
reintroduce silent gaps. An unrecognised framework-shaped declaration is
therefore reported as an unclaimed declaration -- a ranked work list rather than
nothing.
"""

from __future__ import annotations

import pytest

from lineagelens.contracts import (
    PLACEHOLDER,
    AdapterError,
    AdapterRegistry,
    detect_in_observation,
    detect_spi_resources,
    normalise_fqn,
    normalise_http,
    normalise_table,
    normalise_topic,
)
from lineagelens.core import ContractKind, EdgeKind
from lineagelens.extract import SpecExtractor


@pytest.fixture(scope="module")
def extractor():
    return SpecExtractor()


@pytest.fixture(scope="module")
def registry():
    return AdapterRegistry()


def detect(extractor, registry, path, dialect, source, service="app"):
    observation = extractor.extract(
        path=path, content=source, dialect=dialect, content_hash=path,
        service=service, module_path=path.rsplit(".", 1)[0].replace("/", "."),
    )
    return detect_in_observation(observation, registry)


# ---------------------------------------------------------------------------
# §8.3 -- key normalisation
# ---------------------------------------------------------------------------


class TestRouteNormalisation:
    @pytest.mark.parametrize("written", [
        "/api/users/{id}",        # FastAPI, Spring, ASP.NET
        "/api/users/:id",         # Express, NestJS
        "`/api/users/${id}`",     # TypeScript template literal
        "/api/users/<int:id>",    # Flask
        "/api/users/%s",          # format string
        '"/api/users/{user_id}"', # quoted, different parameter name
        "/api/users/*",           # wildcard
    ])
    def test_every_parameter_syntax_collapses_to_one_key(self, written):
        """All of these denote one route and must produce one contract node."""
        assert normalise_http(written, "GET").key == f"GET /api/users/{PLACEHOLDER}"

    def test_origin_is_stripped(self):
        """Same route reached same-origin and cross-origin must be one node."""
        assert (
            normalise_http("https://svc.internal/api/users", "GET").key
            == normalise_http("/api/users", "GET").key
        )

    def test_query_and_fragment_are_not_part_of_the_route(self):
        assert normalise_http("/api/users?page=2#top", "GET").key == "GET /api/users"

    def test_trailing_slash_does_not_split_the_node(self):
        assert (
            normalise_http("/api/users/", "GET").key
            == normalise_http("/api/users", "GET").key
        )

    def test_method_is_part_of_the_key(self):
        """A GET and a POST on one path are different contracts."""
        assert normalise_http("/api/users", "GET").key != normalise_http(
            "/api/users", "POST"
        ).key

    def test_unknown_method_becomes_any(self):
        assert normalise_http("/x", "FROBNICATE").key.startswith("ANY ")

    def test_inline_interpolation_keeps_the_literal_parts(self):
        """`user-${id}.json` is not wholly dynamic; the literals still join."""
        key = normalise_http("/files/user-${id}.json", "GET").key
        assert key == f"GET /files/user-{PLACEHOLDER}.json"


class TestDynamicKeys:
    def test_runtime_concatenation_is_flagged_and_prefixed(self):
        """A key built at runtime has no static join, so it says so (§8.3).

        The literal head is kept as a lead: `/api/` narrows a manual search,
        whereas normalising the whole expression would produce a key containing
        source code.
        """
        result = normalise_http('"/api/" + path', "GET")
        assert result.dynamic is True
        assert result.prefix == "GET /api"
        assert "+" not in result.key

    def test_leading_variable_yields_no_key_at_all(self):
        """Nothing is known statically, so nothing is claimed."""
        result = normalise_http('base + "/x"', "GET")
        assert result.dynamic is True
        assert result.key == ""

    def test_dynamic_key_cannot_join_a_static_one(self):
        """A partial key must not silently match a route sharing its prefix."""
        dynamic = normalise_http('"/api/" + path', "GET")
        static = normalise_http("/api", "GET")
        assert dynamic.key != static.key


class TestOtherKinds:
    def test_fqn_drops_generics_and_normalises_separators(self):
        assert normalise_fqn("Handler<String>").key == "Handler"
        assert normalise_fqn("org::apache::X").key == "org.apache.X"
        assert normalise_fqn("Foo.class").key == "Foo"

    def test_topic_wildcards_are_preserved(self):
        """`orders.*` is a different subscription from `orders.created`.

        Collapsing them would claim a link that does not exist.
        """
        assert normalise_topic('"orders.*"').key != normalise_topic('"orders.created"').key

    def test_table_names_fold_case(self):
        """An ORM model and a raw SQL string must agree."""
        assert normalise_table('"Users"').key == normalise_table("users").key


# ---------------------------------------------------------------------------
# §8.1 -- the join
# ---------------------------------------------------------------------------


class TestCrossFrameworkJoin:
    def test_typescript_client_joins_python_server(self, extractor, registry):
        """The flagship case: frontend fetch reaches a backend route handler.

        Neither side references the other in source. The link exists only
        because both normalise to the same contract key.
        """
        server = detect(
            extractor, registry, "api/routes.py", "python",
            b'@router.get("/api/users/{user_id}")\ndef get_user(user_id: int):\n    return user_id\n',
            service="api",
        )
        client = detect(
            extractor, registry, "web/api.ts", "typescript",
            b"export async function fetchUser(id: number) {\n"
            b"  return fetch(`/api/users/${id}`);\n}\n",
            service="web",
        )
        server.merge(client)

        by_key: dict[str, set[EdgeKind]] = {}
        for edge in server.edges:
            by_key.setdefault(edge.metadata["contract_key"], set()).add(edge.kind)

        joined = [k for k, kinds in by_key.items()
                  if {EdgeKind.EXPOSES, EdgeKind.CONSUMES} <= kinds]
        assert joined == [f"GET /api/users/{PLACEHOLDER}"], (
            f"expected one joined contract, got {by_key}"
        )

    def test_fetch_defaults_to_get(self, extractor, registry):
        """`fetch(url)` with no options is a GET; ANY would fail to join."""
        client = detect(
            extractor, registry, "web/a.ts", "typescript",
            b'export function f() { return fetch("/api/x"); }\n',
        )
        assert any(
            e.metadata["contract_key"].startswith("GET ") for e in client.edges
        )

    def test_a_consumer_with_no_exposer_is_visible(self, extractor, registry):
        """A dangling contract is a finding: external dependency, or a bug."""
        client = detect(
            extractor, registry, "web/a.ts", "typescript",
            b'export function f() { return fetch("/external/thing"); }\n',
        )
        assert client.contracts
        assert all(e.kind is EdgeKind.CONSUMES for e in client.edges)

    def test_many_consumers_share_one_contract_node(self, extractor, registry):
        """Many-to-many falls out of modelling the key as a node (§8.1)."""
        first = detect(
            extractor, registry, "web/a.ts", "typescript",
            b'export function a() { return fetch("/api/x"); }\n',
        )
        second = detect(
            extractor, registry, "web/b.ts", "typescript",
            b'export function b() { return fetch("/api/x"); }\n',
        )
        first.merge(second)
        assert len(first.contracts) == 1
        assert len(first.edges) == 2

    def test_contract_edges_are_heuristic(self, extractor, registry):
        """A route-string match is never a syntactic fact.

        Tiering it separately means an agent can exclude contract edges from a
        query that needs only proven relationships.
        """
        result = detect(
            extractor, registry, "api/r.py", "python",
            b'@app.post("/api/orders")\ndef make(): pass\n',
        )
        assert result.edges
        assert all(e.evidence.tier.value == "heuristic" for e in result.edges)
        assert all(e.provenance.startswith("adapter:") for e in result.edges)


class TestTypeKeyedContracts:
    def test_dubbo_reference_keys_on_the_field_type(self, extractor, registry):
        """`@DubboReference private Greeting greeting;` names its contract by type.

        The annotation carries no string, so the key comes from the annotated
        field's declared type -- recovered by span containment.
        """
        result = detect(
            extractor, registry, "Consumer.java", "java",
            b"class Consumer {\n    @DubboReference\n"
            b"    private GreetingService greeting;\n}\n",
        )
        keys = {c.normalised_key for c in result.contracts.values()}
        assert "GreetingService" in keys
        assert any(e.kind is EdgeKind.CONSUMES for e in result.edges)

    def test_dubbo_service_and_reference_join(self, extractor, registry):
        """Provider and consumer link with no call edge between them in source."""
        provider = detect(
            extractor, registry, "Impl.java", "java",
            b"@DubboService\nclass GreetingServiceImpl implements GreetingService {}\n",
        )
        consumer = detect(
            extractor, registry, "Consumer.java", "java",
            b"class Consumer {\n    @DubboReference\n"
            b"    private GreetingService greeting;\n}\n",
        )
        provider.merge(consumer)
        by_key: dict[str, set[EdgeKind]] = {}
        for edge in provider.edges:
            by_key.setdefault(edge.metadata["contract_key"], set()).add(edge.kind)
        assert any(
            {EdgeKind.EXPOSES, EdgeKind.CONSUMES} <= kinds
            for kinds in by_key.values()
        ), f"provider and consumer did not join: {by_key}"


class TestSpiRegistry:
    def test_meta_inf_services_is_read(self, tmp_path, extractor, registry):
        """The gap `ontology.py` recorded as permanently invisible.

        ``META-INF/services/<interface>`` is a provider declaration in a file no
        parser reads: the filename is the interface and each line an
        implementation.
        """
        src = tmp_path / "Impl.java"
        src.write_text("package org.acme;\nclass Impl implements Iface {}\n")
        services = tmp_path / "META-INF" / "services"
        services.mkdir(parents=True)
        (services / "org.acme.Iface").write_text(
            "# a comment\norg.acme.Impl\n\n"
        )

        observation = extractor.extract(
            path="Impl.java", content=src.read_bytes(), dialect="java",
            content_hash="h", service="app", module_path="org.acme.Impl",
        )
        by_qname: dict[str, list] = {}
        for node in observation.nodes:
            by_qname.setdefault(node.qualified_name, []).append(node)

        result = detect_spi_resources(tmp_path, by_qname)
        assert result.contracts
        contract = next(iter(result.contracts.values()))
        assert contract.kind is ContractKind.SPI
        assert contract.normalised_key == "org.acme.Iface"
        assert [e.kind for e in result.edges] == [EdgeKind.EXPOSES]

    def test_dubbo_alias_form_is_handled(self, tmp_path, extractor):
        """Dubbo writes `alias=FQN` rather than a bare FQN."""
        src = tmp_path / "Impl.java"
        src.write_text("package org.acme;\nclass Impl implements Iface {}\n")
        registry_dir = tmp_path / "META-INF" / "dubbo"
        registry_dir.mkdir(parents=True)
        (registry_dir / "org.acme.Iface").write_text("myalias=org.acme.Impl\n")

        observation = SpecExtractor().extract(
            path="Impl.java", content=src.read_bytes(), dialect="java",
            content_hash="h", service="app", module_path="org.acme.Impl",
        )
        by_qname: dict[str, list] = {}
        for node in observation.nodes:
            by_qname.setdefault(node.qualified_name, []).append(node)

        result = detect_spi_resources(tmp_path, by_qname)
        assert [e.kind for e in result.edges] == [EdgeKind.EXPOSES]

    def test_provider_outside_the_repo_is_a_boundary_not_an_edge(self, tmp_path):
        """A provider from a third-party jar is genuinely external."""
        services = tmp_path / "META-INF" / "services"
        services.mkdir(parents=True)
        (services / "org.acme.Iface").write_text("com.vendor.NotHere\n")

        result = detect_spi_resources(tmp_path, {})
        assert not result.edges
        assert result.boundaries
        assert "not in the index" in result.boundaries[0].detail


# ---------------------------------------------------------------------------
# §8.2 -- a missing adapter must be visible
# ---------------------------------------------------------------------------


class TestUnclaimedFrameworks:
    def test_unknown_framework_is_reported_with_a_sample_key(
        self, extractor, registry
    ):
        """Adapters are data, so a framework nobody wrote one for is a gap.

        Reporting it turns "coverage depends on our adapter library" from a
        silent limitation into a ranked work list.
        """
        result = detect(
            extractor, registry, "api/legacy.py", "python",
            b'@my_inhouse_router.route("/api/legacy/{id}")\ndef legacy(): pass\n',
        )
        assert not result.edges, "no adapter should have claimed this"
        summary = result.unclaimed_summary()
        assert summary
        assert summary[0]["name"] == "route"
        assert summary[0]["sample_key"] == '"/api/legacy/{id}"'
        assert "api/legacy.py" in str(summary[0]["example"])

    def test_uses_are_counted_so_the_list_can_be_ranked(self, extractor, registry):
        result = detect(
            extractor, registry, "api/legacy.py", "python",
            b'@custom.bind("/a/{x}")\ndef a(): pass\n\n'
            b'@custom.bind("/b/{y}")\ndef b(): pass\n',
        )
        summary = result.unclaimed_summary()
        assert summary[0]["uses"] == 2

    def test_ordinary_decorators_are_not_reported(self, extractor, registry):
        """A plain `@dataclass` is not a framework binding.

        Reporting every decorator would make the list unreadable and therefore
        unread, so only route/topic-shaped arguments qualify.
        """
        result = detect(
            extractor, registry, "m.py", "python",
            b"@dataclass\nclass A:\n    x: int = 1\n\n"
            b"@functools.lru_cache(maxsize=128)\ndef f(): pass\n",
        )
        assert result.unclaimed_summary() == []


# ---------------------------------------------------------------------------
# adapter definitions
# ---------------------------------------------------------------------------


class TestAdapterDefinitions:
    def test_shipped_adapters_load(self, registry):
        adapters = registry.all_adapters()
        assert len(adapters) > 20
        assert {a.role for a in adapters} == {"exposes", "consumes"}

    def test_every_name_is_a_string(self, registry):
        """YAML 1.1 reads bare `on`/`off`/`yes`/`no` as booleans.

        An unquoted `on` in a names list reached the matcher as ``True`` and
        crashed the index, so the loader now rejects non-strings outright.
        """
        for adapter in registry.all_adapters():
            for name in adapter.names:
                assert isinstance(name, str), f"{adapter.id}: {name!r}"

    def test_boolean_name_is_rejected_with_a_useful_message(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(
            "- id: bad.adapter\n"
            "  lang: python\n"
            "  match: call\n"
            "  names: [on]\n"
            "  contract: {kind: topic, role: consumes}\n"
        )
        with pytest.raises(AdapterError, match="quote it"):
            AdapterRegistry([tmp_path]).all_adapters()

    def test_digest_is_stable_and_content_sensitive(self, tmp_path):
        """An adapter edit changes which contracts exist, so it must invalidate
        the index the same way a grammar or spec change does (§11)."""
        assert AdapterRegistry().digest() == AdapterRegistry().digest()

        (tmp_path / "one.yaml").write_text(
            "- id: a\n  lang: python\n  match: call\n  names: [f]\n"
            "  contract: {kind: env, role: consumes}\n"
        )
        first = AdapterRegistry([tmp_path]).digest()
        (tmp_path / "one.yaml").write_text(
            "- id: a\n  lang: python\n  match: call\n  names: [g]\n"
            "  contract: {kind: env, role: consumes}\n"
        )
        assert AdapterRegistry([tmp_path]).digest() != first

    def test_project_local_adapters_are_loaded(self, tmp_path, extractor):
        """A team describes an in-house framework without patching the package."""
        local = tmp_path / ".lineagelens" / "adapters"
        local.mkdir(parents=True)
        (local / "inhouse.yaml").write_text(
            "- id: inhouse.route\n"
            "  lang: python\n"
            "  match: decorator\n"
            "  names: [my_inhouse_router.route]\n"
            "  methods: [any]\n"
            "  contract: {kind: http_route, role: exposes, key_arg: 0}\n"
        )
        from lineagelens.contracts import project_adapter_roots

        registry = AdapterRegistry(project_adapter_roots(tmp_path))
        result = detect(
            extractor, registry, "api/legacy.py", "python",
            b'@my_inhouse_router.route("/api/legacy/{id}")\ndef legacy(): pass\n',
        )
        assert result.edges, "the project-local adapter should have claimed this"
        assert result.unclaimed_summary() == [], "and it is no longer unclaimed"
