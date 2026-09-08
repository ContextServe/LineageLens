"""Framework adapters (issue #51 §8.2).

Frameworks are too numerous to hardcode and every organisation has in-house
ones, so an adapter is a YAML record rather than Python. Adding NestJS, or a
company's internal RPC layer, means adding a file.

An adapter matches an *observation* the extractor already produced -- a
decorator, an annotation, a call, or a resource file -- and states which
contract key it declares and on which side. It never resolves anything and
never creates an edge; it produces a :class:`~lineagelens.core.types.Contract`
and a role, and the resolver turns those into ``EXPOSES``/``CONSUMES``.

The join is the whole mechanism: a FastAPI handler and a TypeScript ``fetch``
both normalise to ``GET /api/users/{*}``, so both attach to one contract node
and the cross-language path is a two-hop walk through it. Nothing about the two
sides needs to know the other exists.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..core import (
    Contract,
    ContractKind,
    EdgeKind,
    RefKind,
    UnresolvedRef,
    contract_id,
)
from .normalise import NormalisedKey, normalise

logger = logging.getLogger(__name__)

#: Directory holding the shipped adapter definitions. A project may add its own
#: via ``ADAPTER_DIR_ENV`` or a ``.lineagelens/adapters/`` directory.
ADAPTER_ROOT = Path(__file__).resolve().parent / "definitions"

#: Which observed reference kind an adapter can match against.
MATCH_KINDS: dict[str, RefKind] = {
    "decorator": RefKind.DECORATE,
    "annotation": RefKind.DECORATE,
    "call": RefKind.CALL,
    "type": RefKind.TYPE,
    "import": RefKind.IMPORT,
}

#: Role -> edge kind. ``exposes`` means "this symbol serves the contract";
#: ``consumes`` means "this symbol depends on it".
ROLE_EDGES: dict[str, EdgeKind] = {
    "exposes": EdgeKind.EXPOSES,
    "consumes": EdgeKind.CONSUMES,
}


class AdapterError(RuntimeError):
    """An adapter definition is malformed."""


@dataclass(frozen=True, slots=True)
class Adapter:
    """One framework binding rule."""

    id: str
    languages: frozenset[str]
    match: str                      # a MATCH_KINDS key, or "resource"
    kind: ContractKind
    role: str                       # exposes | consumes
    #: Names that trigger this adapter, matched against the observed reference
    #: text (the decorator name, the callee, the annotation).
    names: tuple[str, ...] = ()
    #: HTTP verbs this adapter covers. When the verb is part of the name
    #: (``app.get``, ``@GetMapping``) it is derived from the matched name; when
    #: it is an argument (``axios.request``) it comes from ``method_arg``.
    methods: tuple[str, ...] = ()
    #: Which captured argument holds the key. ``0`` is the first positional.
    key_arg: int = 0
    #: Name of a keyword/annotation argument holding the key, preferred over
    #: ``key_arg`` when present (``@RequestMapping(value = "/x")``).
    key_name: str = ""
    #: For ``match: resource`` -- a glob relative to the repository root.
    resource_glob: str = ""
    #: When true the key is the *type* of the annotated declaration rather than
    #: an argument. Dubbo's ``@Reference private Greeting greeting;`` names its
    #: contract by the field's interface, not by a string.
    key_from_type: bool = False
    #: Evidence label recorded on the resulting edge, so ``explain()`` can name
    #: the adapter that produced it.
    evidence: str = "contract_adapter"

    def matches(self, ref: UnresolvedRef) -> bool:
        expected = MATCH_KINDS.get(self.match)
        if expected is None or ref.ref_kind is not expected:
            return False
        if not self.names:
            return True
        text = ref.ref_text
        receiver = ref.receiver_hint or ""
        for name in self.names:
            if text == name or text.endswith(f".{name}"):
                return True
            # `app.get(...)` arrives as name=`get`, receiver=`app`. An adapter
            # written as `app.get` should match that shape too.
            if "." in name:
                obj, _, member = name.rpartition(".")
                if text == member and (receiver == obj or receiver.endswith(f".{obj}")):
                    return True
        return False

    def method_for(self, ref: UnresolvedRef) -> str:
        """HTTP verb for this observation.

        Three sources, in order:

        1. **An options argument.** ``fetch(url, {method: "POST"})`` carries the
           verb in its second argument. Without reading it, every ``fetch``
           would be keyed ``GET`` and a POST client would silently fail to join
           its POST route -- a wrong answer rather than a missing one.
        2. **The matched name.** ``app.post``, ``@DeleteMapping``: the common
           case, where the framework encodes the verb in the API.
        3. **The adapter's first declared method**, as the documented default.
        """
        if self.kind is not ContractKind.HTTP_ROUTE:
            return "ANY"

        explicit = _method_from_options(ref)
        if explicit:
            return explicit

        lowered = ref.ref_text.lower()
        for verb in self.methods:
            token = verb.lower()
            if lowered == token or lowered.endswith(token) or token in lowered:
                return verb.upper()
        return "ANY" if not self.methods else self.methods[0].upper()

    def key_from(self, ref: UnresolvedRef) -> str:
        """Pull the raw contract key out of an observation.

        Returns ``""`` when the observation carries no usable key -- a route
        decorator with no argument, say. The caller skips it rather than
        registering an empty contract.
        """
        if self.key_from_type:
            return ref.metadata.get("declared_type") or ref.receiver_hint or ""

        args: Sequence[dict[str, Any]] = ref.metadata.get("args") or ()
        if self.key_name:
            for arg in args:
                text = str(arg.get("text", ""))
                if text.startswith((f"{self.key_name}=", f"{self.key_name} =")):
                    return text.split("=", 1)[1]
        for arg in args:
            if arg.get("index") == self.key_arg:
                return str(arg.get("text", ""))
        return ""


@dataclass(slots=True)
class ContractMatch:
    """An adapter's verdict about one observation."""

    contract: Contract
    role: str
    ref: UnresolvedRef
    adapter: Adapter
    normalised: NormalisedKey

    @property
    def edge_kind(self) -> EdgeKind:
        return ROLE_EDGES[self.role]


class AdapterRegistry:
    """Loads adapters and applies them to observations."""

    def __init__(self, roots: Iterable[Path] | None = None) -> None:
        self.roots = list(roots) if roots is not None else [ADAPTER_ROOT]
        self._adapters: list[Adapter] = []
        self._by_lang: dict[str, list[Adapter]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        for root in self.roots:
            if not root.is_dir():
                continue
            # Sorted, so adapter order -- and therefore any tie-break between
            # two adapters matching one observation -- is deterministic (§11).
            for path in sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml")):
                self._adapters.extend(self._load_file(path))

        for adapter in self._adapters:
            for lang in adapter.languages:
                self._by_lang.setdefault(lang, []).append(adapter)
        self._loaded = True

    def _load_file(self, path: Path) -> list[Adapter]:
        try:
            raw = yaml.safe_load(path.read_text("utf-8")) or []
        except (OSError, yaml.YAMLError) as exc:
            raise AdapterError(f"{path}: {exc}") from exc
        if not isinstance(raw, list):
            raise AdapterError(f"{path}: expected a list of adapters")

        out: list[Adapter] = []
        for entry in raw:
            try:
                contract = entry["contract"]
                languages = entry["lang"]
                # Coerced and validated, because YAML 1.1 turns bare `on`,
                # `off`, `yes` and `no` into booleans. An unquoted `on` in a
                # names list reached `matches()` as True and crashed the whole
                # index; a malformed adapter must fail here, with the file and
                # the offending entry named.
                names = _as_names(entry.get("names", ()), path, entry["id"])
                out.append(Adapter(
                    id=entry["id"],
                    languages=frozenset(
                        [languages] if isinstance(languages, str) else languages
                    ),
                    match=entry["match"],
                    kind=ContractKind(contract["kind"]),
                    role=contract["role"],
                    names=names,
                    methods=tuple(entry.get("methods", ())),
                    key_arg=int(contract.get("key_arg", 0)),
                    key_name=str(contract.get("key_name", "")),
                    resource_glob=str(entry.get("path", "")),
                    key_from_type=bool(contract.get("key_from_type", False)),
                    evidence=str(entry.get("evidence", f"adapter:{entry['id']}")),
                ))
            except (KeyError, ValueError, TypeError) as exc:
                raise AdapterError(f"{path}: malformed adapter {entry!r}: {exc}") from exc
        return out

    def adapters_for(self, lang: str) -> list[Adapter]:
        self._load()
        return self._by_lang.get(lang, [])

    def all_adapters(self) -> list[Adapter]:
        self._load()
        return list(self._adapters)

    def digest(self) -> str:
        """Hash of every adapter definition, for ``graph_meta.adapter_digest``.

        Editing an adapter changes which contracts exist, so it must invalidate
        the index the same way a grammar or spec change does (§11).
        """
        import hashlib

        self._load()
        h = hashlib.blake2b(digest_size=16)
        for adapter in sorted(self._adapters, key=lambda a: a.id):
            h.update(repr(adapter).encode())
        return h.hexdigest()

    def apply(self, ref: UnresolvedRef, lang: str) -> ContractMatch | None:
        """First adapter that claims this observation, or ``None``.

        First rather than all: two adapters claiming one observation means the
        definitions overlap, and producing both contracts would double-count
        the same declaration. Load order is sorted, so the choice is stable.
        """
        for adapter in self.adapters_for(lang):
            if not adapter.matches(ref):
                continue
            raw_key = adapter.key_from(ref)
            if not raw_key:
                continue
            normalised = normalise(
                adapter.kind.value, raw_key, adapter.method_for(ref)
            )
            if not normalised.key:
                continue
            contract = Contract(
                id=contract_id(adapter.kind.value, normalised.key),
                kind=adapter.kind,
                key=raw_key,
                normalised_key=normalised.key,
                metadata={"adapter": adapter.id, "dynamic": normalised.dynamic},
            )
            return ContractMatch(
                contract=contract, role=adapter.role, ref=ref,
                adapter=adapter, normalised=normalised,
            )
        return None


#: `method: "POST"` inside an options object, in any of the six languages'
#: object syntaxes. Matched on the captured argument text rather than parsed,
#: because the argument arrives as source and a full expression parse would be
#: the wrong tool for reading one literal.
_METHOD_OPTION = re.compile(
    r"""["']?method["']?\s*[:=]\s*["'](?P<verb>[A-Za-z]+)["']""",
    re.IGNORECASE,
)


def _method_from_options(ref: UnresolvedRef) -> str:
    """An explicit HTTP verb in a call's options argument, or ``""``."""
    for arg in ref.metadata.get("args") or ():
        match = _METHOD_OPTION.search(str(arg.get("text", "")))
        if match:
            return match.group("verb").upper()
    return ""


def _as_names(raw: object, path: Path, adapter_id: str) -> tuple[str, ...]:
    """Validate an adapter's ``names`` list.

    Rejects non-strings rather than coercing them. ``True`` came from an
    unquoted YAML ``on``, and silently turning it into ``"True"`` would make the
    adapter match nothing while looking fine -- the class of silent gap this
    project exists to remove.
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise AdapterError(f"{path}: adapter {adapter_id!r} has a non-list `names`")
    for name in raw:
        if not isinstance(name, str):
            raise AdapterError(
                f"{path}: adapter {adapter_id!r} has a non-string name {name!r}. "
                f"YAML 1.1 reads bare on/off/yes/no/y/n as booleans -- quote it."
            )
    return tuple(raw)


def project_adapter_roots(project_root: Path) -> list[Path]:
    """Adapter directories to load: the shipped set, then the project's own.

    A project's ``.lineagelens/adapters/`` is loaded after the built-ins, so a
    team can describe an in-house framework without patching the package.
    """
    roots = [ADAPTER_ROOT]
    local = project_root / ".lineagelens" / "adapters"
    if local.is_dir():
        roots.append(local)
    return roots
