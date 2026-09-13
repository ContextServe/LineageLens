"""Canonical node identity.

Schema 3 had two incompatible ID namespaces in one graph: the Python analyser
minted ``module.Class.method`` while the tree-sitter analyser minted
``path/to/file.java::Class.method``. Relation *targets* used one and *sources*
the other, so on Apache Dubbo all 2,593 edges had a dangling source and the
graph was 100% non-traversable while reporting success.

One scheme, used by everything::

    qualified_name = <service>/<lang>/<module-path>#<member>[/<member>...]
    signature_hash = h(normalised parameter types, arity)   # "" for non-callables
    id             = h(service, lang, qualified_name, signature_hash)

Four properties this has to have, each of which rules something out:

* **Stable across extractors.** Tier A and Tier B observing the same symbol must
  converge on one node, so IDs derive only from declared structure -- never from
  which tool did the observing.
* **Stable across edits.** Nothing may derive from a line number. Deriving an ID
  from a position means editing line 10 renames the symbol on line 400 and
  invalidates every edge touching it.
* **Collision-free for overloads.** Java, C#, C++ and Go methods on distinct
  receivers all permit same-name callables in one scope, so the signature is
  part of the key.
* **Deterministic.** Never ``hash()`` -- Python randomises it per process, which
  would make the store differ between runs and break §11.
"""

from __future__ import annotations

import hashlib
import re

#: Hex digits kept from each digest. 128 bits: collision-free at any repository
#: size, and short enough to stay readable in a ``sqlite3`` shell.
_DIGEST_HEX = 32

#: Separates the service / language / module-path segments of a qualified name.
PATH_SEP = "/"
#: Separates the module path from the member chain.
MEMBER_SEP = "#"
#: Separates nested members (``Outer#Inner/method``).
NESTED_SEP = "/"


def _digest(*parts: str) -> str:
    """Stable hash of ``parts``.

    Parts are joined with ``\\x00``, which cannot occur in a source identifier,
    so ``("ab", "c")`` and ``("a", "bc")`` cannot collide.

    blake2b rather than blake3: it is in the standard library, and adding a
    dependency to hash four short strings is not worth it. The digest is an
    opaque key, so the choice of function is not observable beyond determinism.
    """
    payload = "\x00".join(parts).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=_DIGEST_HEX // 2).hexdigest()


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def normalise_type(type_ref: str) -> str:
    """Collapse a type expression to a comparable form.

    Whitespace inside generics is the main offender: ``Map<String, Integer>`` and
    ``Map<String,Integer>`` denote the same type and must hash alike, or the two
    declarations of one overload land on different nodes.

    Deliberately *not* resolved to a fully-qualified name -- that is Tier B's job
    (§7.2), and this function must stay usable before any resolver has run.
    """
    if not type_ref:
        return ""
    collapsed = _WS.sub(" ", type_ref.strip())
    for token in ("<", ">", ",", "[", "]", "(", ")", "&", "|", "*"):
        collapsed = collapsed.replace(f" {token}", token).replace(f"{token} ", token)
    return collapsed


def signature_hash(param_types: list[str] | tuple[str, ...] | None, arity: int | None = None) -> str:
    """Hash of a callable's parameter list, or ``""`` for non-callables.

    Order is significant and preserved: ``f(int, str)`` and ``f(str, int)`` are
    different overloads. Arity is folded in so that a language reporting only
    counts (no types available yet) still separates ``f(a)`` from ``f(a, b)``.

    An empty parameter list is not the same as "not a callable": a zero-arg
    method hashes ``arity=0`` with no types, while a field passes ``None`` and
    gets ``""``.
    """
    if param_types is None and arity is None:
        return ""
    types = [normalise_type(t) for t in (param_types or ())]
    count = arity if arity is not None else len(types)
    return _digest("sig", str(count), *types)


def normalise_module_path(module_path: str) -> str:
    """Normalise a dotted or slashed module path to slash-separated segments.

    Extractors report module paths in whatever their language uses -- dots for
    Python and Java, slashes for Go, ``::`` for Rust. They converge here so a
    qualified name has one shape regardless of source language.
    """
    if not module_path:
        return ""
    unified = module_path.replace("::", PATH_SEP).replace(".", PATH_SEP).replace("\\", PATH_SEP)
    return PATH_SEP.join(seg for seg in unified.split(PATH_SEP) if seg)


# ---------------------------------------------------------------------------
# qualified names
# ---------------------------------------------------------------------------

def qualified_name(
    service: str,
    lang: str,
    module_path: str,
    members: list[str] | tuple[str, ...] = (),
) -> str:
    """Assemble the human-readable, queryable name.

    Kept legible on purpose: it is what appears in MCP responses and what a
    developer greps for. The opaque :func:`node_id` is the join key; this is the
    display and search key, and both are stored.
    """
    prefix = PATH_SEP.join(p for p in (service, lang, normalise_module_path(module_path)) if p)
    if not members:
        return prefix
    return f"{prefix}{MEMBER_SEP}{NESTED_SEP.join(members)}"


def anonymous_member(kind: str, ordinal: int) -> str:
    """Name for a construct the source did not name.

    Lambdas, closures and anonymous classes need a stable member name. It is
    derived from the **ordinal position among same-kind siblings within the
    parent**, never from a line number -- so inserting a statement above a lambda
    does not rename it, whereas a line-derived name would invalidate every edge
    touching it on every edit.
    """
    return f"<{kind}:{ordinal}>"


# ---------------------------------------------------------------------------
# ids
# ---------------------------------------------------------------------------

def node_id(service: str, lang: str, qname: str, sig_hash: str = "") -> str:
    """The canonical node key.

    ``service`` and ``lang`` are already inside ``qname``, but are hashed
    separately as well: it keeps the inputs explicit at every call site, and
    makes a malformed qualified name fail loudly here rather than silently
    aliasing onto another node.
    """
    if not qname:
        raise ValueError("qualified_name is required to mint a node id")
    return _digest("node", service, lang, qname, sig_hash)


def service_id(name: str, root: str) -> str:
    """Identity of a deployment unit (§7.4).

    Includes the root path, because a monorepo can legitimately contain two
    services with the same manifest name in different directories.
    """
    return _digest("service", name, root.replace("\\", PATH_SEP).strip(PATH_SEP))


def contract_id(kind: str, normalised_key: str) -> str:
    """Identity of a cross-framework contract (§8.1).

    Deliberately independent of service and language: joining a TypeScript
    ``fetch`` to a Python route handler works precisely *because* both sides
    compute the same id from ``(kind, key)`` alone.

    ``kind`` namespaces the key, so an HTTP route ``orders`` and a Kafka topic
    ``orders`` stay distinct.

    The caller must pass an already-normalised key -- see
    ``contracts.normalise.contract_key``. Normalising here would hide the fact
    that normalisation is lossy and needs its own tests.
    """
    if not normalised_key:
        raise ValueError("contract key is required to mint a contract id")
    return _digest("contract", kind, normalised_key)


def file_node_id(service: str, path: str) -> str:
    """Identity of a ``file``-kind node.

    Path-derived rather than module-derived: a file is a filesystem fact, and two
    files can map to the same module path (``__init__.py`` in namespace packages,
    Go files sharing a package).
    """
    return _digest("file", service, path.replace("\\", PATH_SEP))
