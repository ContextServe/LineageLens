"""In-memory symbol index, built once per resolve pass.

The resolver needs to answer "what could this name refer to, from here" many
times per file. Doing that against SQLite per reference would dominate the
index time, so the node set is loaded once into lookup tables shaped for the
three questions resolution actually asks:

* what is in scope at this node (the containment chain)
* what members does this type have, including inherited ones
* what does this file's import list bring into scope

Nothing here resolves anything. It answers candidate questions; the resolver
decides, and records an ambiguity when the answer is not unique.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from ..core import EdgeKind, Node, NodeKind

#: Kinds that own members, for member lookup and inheritance walks.
TYPE_KINDS = frozenset({
    NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ENUM,
    NodeKind.STRUCT, NodeKind.TRAIT, NodeKind.ANNOTATION,
})

#: Receiver expressions that mean "the enclosing type", per language. Kept here
#: rather than in a spec because the resolver is the only consumer and the set
#: is tiny and closed.
SELF_RECEIVERS = frozenset({"self", "this", "cls", "Self", "&self", "&mut self"})


class SymbolIndex:
    """Lookup tables over the node set of one project."""

    __slots__ = (
        "_by_id", "_by_name", "_by_qname", "_inherits", "_members", "_modules",
    )

    def __init__(self, nodes: Iterable[Node]) -> None:
        self._by_id: dict[str, Node] = {}
        self._by_qname: dict[str, list[Node]] = defaultdict(list)
        self._by_name: dict[str, list[Node]] = defaultdict(list)
        self._members: dict[str, dict[str, list[Node]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._modules: dict[str, Node] = {}
        #: ``{type_node_id: [base_node_id, ...]}``, populated by
        #: :meth:`add_inheritance` once INHERITS/IMPLEMENTS edges are resolved.
        self._inherits: dict[str, list[str]] = defaultdict(list)

        for node in nodes:
            self._by_id[node.id] = node
            self._by_qname[node.qualified_name].append(node)
            self._by_name[node.name].append(node)
            if node.parent_id:
                self._members[node.parent_id][node.name].append(node)
            if node.kind is NodeKind.MODULE:
                self._modules[node.qualified_name] = node

    # ---- basic lookup -----------------------------------------------------

    def get(self, node_id: str) -> Node | None:
        return self._by_id.get(node_id)

    def by_qualified_name(self, qname: str) -> list[Node]:
        return list(self._by_qname.get(qname, ()))

    def by_name(self, name: str) -> list[Node]:
        """Every node with this bare name, anywhere in the project.

        The candidate pool of last resort. Returning it is *not* a resolution:
        the resolver only ever records these as an ambiguity, because picking
        one is precisely what schema 3 did -- ``possible_targets[0]`` -- and it
        is why the Dubbo graph claimed 2,593 resolved edges with zero
        traversable paths.
        """
        return list(self._by_name.get(name, ()))

    def module(self, qname: str) -> Node | None:
        return self._modules.get(qname)

    def __len__(self) -> int:
        return len(self._by_id)

    # ---- containment ------------------------------------------------------

    def ancestors(self, node_id: str) -> list[Node]:
        """The containment chain from a node's parent outward to its module.

        Guards against a cycle: a malformed containment tree would otherwise
        spin here, and the extractor's tree is only checked in tests.
        """
        chain: list[Node] = []
        seen = {node_id}
        node = self._by_id.get(node_id)
        while node is not None and node.parent_id and node.parent_id not in seen:
            seen.add(node.parent_id)
            parent = self._by_id.get(node.parent_id)
            if parent is None:
                break
            chain.append(parent)
            node = parent
        return chain

    def enclosing_type(self, node_id: str) -> Node | None:
        """Nearest enclosing class/interface/struct/trait, or ``None``."""
        node = self._by_id.get(node_id)
        if node is not None and node.kind in TYPE_KINDS:
            return node
        for ancestor in self.ancestors(node_id):
            if ancestor.kind in TYPE_KINDS:
                return ancestor
        return None

    def enclosing_module(self, node_id: str) -> Node | None:
        node = self._by_id.get(node_id)
        if node is not None and node.kind is NodeKind.MODULE:
            return node
        for ancestor in self.ancestors(node_id):
            if ancestor.kind is NodeKind.MODULE:
                return ancestor
        return None

    # ---- members and inheritance ------------------------------------------

    def add_inheritance(self, owner_id: str, base_id: str) -> None:
        """Record a resolved base, so member lookup can walk it.

        Called by the resolver after INHERITS/IMPLEMENTS edges are created.
        Inheritance is therefore only followed once it has been *resolved* --
        an unresolved base contributes nothing rather than being guessed at.
        """
        if base_id not in self._inherits[owner_id]:
            self._inherits[owner_id].append(base_id)

    def bases(self, type_id: str) -> list[str]:
        """Base type ids in breadth-first order, cycle-safe."""
        order: list[str] = []
        seen = {type_id}
        queue = list(self._inherits.get(type_id, ()))
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            order.append(current)
            queue.extend(self._inherits.get(current, ()))
        return order

    def direct_members(self, owner_id: str, name: str) -> list[Node]:
        return list(self._members.get(owner_id, {}).get(name, ()))

    def members(self, owner_id: str, name: str) -> list[Node]:
        """Members named ``name`` on ``owner_id`` or any resolved base.

        Own members shadow inherited ones, so the search stops at the first
        level that has a match rather than unioning the whole hierarchy -- a
        subclass override must not come back alongside the base it overrides.
        """
        own = self.direct_members(owner_id, name)
        if own:
            return own
        for base in self.bases(owner_id):
            inherited = self.direct_members(base, name)
            if inherited:
                return inherited
        return []

    def all_members(self, owner_id: str) -> list[Node]:
        return [n for group in self._members.get(owner_id, {}).values() for n in group]

    # ---- scope resolution -------------------------------------------------

    def in_scope(self, from_node: str, name: str) -> list[Node]:
        """Candidates for a bare ``name`` written inside ``from_node``.

        Walks outward: the node's own members (locals and parameters), then each
        enclosing scope, then that scope's resolved bases if it is a type. The
        first level with a match wins, which is what lexical scoping means -- a
        local shadows a field shadows a module-level name.
        """
        own = self.direct_members(from_node, name)
        if own:
            return own

        for scope in self.ancestors(from_node):
            found = self.direct_members(scope.id, name)
            if found:
                return found
            if scope.kind in TYPE_KINDS:
                inherited = self.members(scope.id, name)
                if inherited:
                    return inherited
        return []

    def sibling_module_symbol(self, from_node: str, name: str) -> list[Node]:
        """A top-level name in the same module as ``from_node``."""
        module = self.enclosing_module(from_node)
        if module is None:
            return []
        return self.direct_members(module.id, name)


def types_only(nodes: Sequence[Node]) -> list[Node]:
    return [n for n in nodes if n.kind in TYPE_KINDS]


def callables_only(nodes: Sequence[Node]) -> list[Node]:
    return [n for n in nodes if n.is_callable]


def values_only(nodes: Sequence[Node]) -> list[Node]:
    """Nodes a data-flow edge may terminate on (§9)."""
    return [n for n in nodes if n.is_value]


#: Which node kinds are a plausible target for each edge kind. Used to narrow a
#: candidate set before declaring an ambiguity: a CALL to a name that matches
#: both a class and a method is only ambiguous if both are callable.
TARGET_KINDS: dict[EdgeKind, frozenset[NodeKind]] = {
    EdgeKind.CALLS: frozenset({
        NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CONSTRUCTOR,
        NodeKind.PROPERTY, NodeKind.CLASS,  # a class call is construction
    }),
    EdgeKind.INSTANTIATES: frozenset(TYPE_KINDS),
    EdgeKind.INHERITS: frozenset(TYPE_KINDS),
    EdgeKind.IMPLEMENTS: frozenset(TYPE_KINDS),
    EdgeKind.HAS_TYPE: frozenset(TYPE_KINDS),
    EdgeKind.THROWS: frozenset(TYPE_KINDS),
    EdgeKind.DECORATES: frozenset({
        NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS, NodeKind.ANNOTATION,
    }),
    EdgeKind.READS: frozenset({
        NodeKind.FIELD, NodeKind.PARAMETER, NodeKind.VARIABLE, NodeKind.CONSTANT,
    }),
    EdgeKind.WRITES: frozenset({
        NodeKind.FIELD, NodeKind.PARAMETER, NodeKind.VARIABLE, NodeKind.CONSTANT,
    }),
    EdgeKind.IMPORTS: frozenset({NodeKind.MODULE, NodeKind.PACKAGE}) | TYPE_KINDS | {
        NodeKind.FUNCTION, NodeKind.CONSTANT, NodeKind.VARIABLE,
    },
}
