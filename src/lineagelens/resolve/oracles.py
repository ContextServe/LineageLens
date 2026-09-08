"""Tier B resolver oracles (issue #51 §7.2).

Tier B is **required**, not an enhancement. Tier A cannot resolve a Java
overload, a Go structurally-satisfied interface, or a TypeScript structural
type -- and schema 3's response to an unanswerable reference was to guess,
which is how it produced 2,593 Dubbo edges that all claimed
``resolution=resolved`` and none of which traversed.

The interface is deliberately two methods::

    resolve(ref)   -> ResolvedTarget | None    what does this name point at
    type_of(span)  -> TypeName | None           what type is this expression

That narrowness is what makes six language toolchains tractable. An oracle
never mints node ids, never writes edges, and never describes structure -- it
answers those two questions and the resolver does the rest.

Toolchain policy
----------------

Resolution order per language, from ``§7.2``:

1. **Vendored** -- ships as a Python wheel, so no external requirement. jedi
   (Python) is here; a bundled ``tsserver`` for TypeScript belongs here too.
2. **Detected** -- a system toolchain found on PATH or via an env var
   (``javac``, ``gopls``, ``rust-analyzer``, ``dotnet``).
3. **Container** -- ``--toolchain-image`` runs a missing resolver in Docker.
4. **Refuse that language** -- files are recorded ``skip_reason='missing_tier_b'``
   and the capability matrix marks the language unavailable. They are *not*
   extracted at Tier A only, because a Tier-A-only Java graph is exactly the
   fabricated-edge failure above.

``--allow-tier-a-only=<langs>`` overrides step 4 explicitly. Every edge it
produces is stamped ``heuristic`` / ``tier_a_only`` and reported in the
completeness envelope, so the degradation is never silent.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..core import Evidence, NodeKind, Resolution, ResolvedTarget, Span, UnresolvedRef

logger = logging.getLogger(__name__)

#: Sentinel for "PATH not yet probed". Distinct from None, which means probed
#: and not found.
_UNPROBED = object()


class OracleAvailability(str):
    """How an oracle became available, for the capability matrix (§12)."""

    VENDORED = "vendored"
    DETECTED = "detected"
    CONTAINER = "container"
    MISSING = "missing"


@runtime_checkable
class ResolverOracle(Protocol):
    """A language toolchain, asked only what it is uniquely able to answer."""

    name: str
    languages: frozenset[str]

    def available(self) -> str:
        """One of the :class:`OracleAvailability` values."""
        ...

    def version(self) -> str:
        ...

    def resolve(self, ref: UnresolvedRef) -> ResolvedTarget | None:
        ...

    def type_of(self, span: Span, file_path: str) -> str | None:
        ...


@dataclass(slots=True)
class _Base:
    """Shared plumbing. Subclasses implement the two answering methods."""

    project_root: Path = Path()

    def version(self) -> str:
        return "unknown"

    def resolve(self, ref: UnresolvedRef) -> ResolvedTarget | None:
        return None

    def type_of(self, span: Span, file_path: str) -> str | None:
        return None


@dataclass(slots=True)
class JediOracle(_Base):
    """Python, via jedi. Vendored: jedi is a wheel, so nothing external needed.

    jedi is genuinely useful for the case Tier A cannot reach -- inferring the
    type of a receiver that was never annotated, so ``repo.save()`` resolves
    when ``repo`` came from a factory rather than from a typed parameter.
    """

    name: str = "jedi"
    languages: frozenset[str] = frozenset({"python"})

    def available(self) -> str:
        try:
            import jedi  # noqa: F401
        except ImportError:
            return OracleAvailability.MISSING
        return OracleAvailability.VENDORED

    def version(self) -> str:
        try:
            import jedi

            return getattr(jedi, "__version__", "unknown")
        except ImportError:
            return "missing"

    def resolve(self, ref: UnresolvedRef) -> ResolvedTarget | None:
        """Ask jedi what a name at a position refers to.

        Returns ``None`` rather than a guess whenever jedi is unsure or offers
        more than one answer: an ambiguous oracle answer is no better than an
        ambiguous Tier A candidate set, and the resolver already knows how to
        record that honestly.
        """
        try:
            import jedi
        except ImportError:
            return None

        path = self.project_root / ref.file_path
        if not path.is_file():
            return None

        try:
            script = jedi.Script(path=str(path), project=jedi.Project(str(self.project_root)))
            names = script.goto(
                line=ref.span.start_line,
                column=ref.span.start_col,
                follow_imports=True,
            )
        except Exception as exc:  # jedi raises a wide variety on odd input
            logger.debug("jedi could not resolve %s at %s: %s", ref.ref_text, path, exc)
            return None

        in_project = [n for n in names if n.module_path and self._inside(n.module_path)]
        if len(in_project) != 1:
            # Zero means external; more than one means jedi is guessing too.
            return None

        found = in_project[0]
        return ResolvedTarget(
            qualified_name=found.full_name or found.name,
            kind=_JEDI_KINDS.get(found.type),
            evidence=Evidence.fact("jedi_goto"),
            resolution=Resolution.EXACT,
        )

    def type_of(self, span: Span, file_path: str) -> str | None:
        try:
            import jedi
        except ImportError:
            return None
        path = self.project_root / file_path
        if not path.is_file():
            return None
        try:
            script = jedi.Script(path=str(path), project=jedi.Project(str(self.project_root)))
            inferred = script.infer(line=span.start_line, column=span.start_col)
        except Exception:
            return None
        return inferred[0].name if len(inferred) == 1 else None

    def _inside(self, module_path: Path) -> bool:
        try:
            Path(module_path).relative_to(self.project_root)
        except ValueError:
            return False
        return True


_JEDI_KINDS: dict[str, NodeKind] = {
    "class": NodeKind.CLASS,
    "function": NodeKind.FUNCTION,
    "module": NodeKind.MODULE,
    "instance": NodeKind.VARIABLE,
    "param": NodeKind.PARAMETER,
    "statement": NodeKind.VARIABLE,
    "property": NodeKind.PROPERTY,
}


@dataclass(slots=True)
class ToolchainOracle(_Base):
    """A detected system toolchain.

    Present as a real availability check with no answering implementation yet:
    ``resolve`` inherits the base's ``None``. That is deliberate rather than a
    stub -- it lets the capability matrix report the honest state ("javac
    detected, resolution not wired") instead of either claiming the language is
    unsupported or silently answering from Tier A and calling it a fact.

    Wiring each toolchain is per-language work and is tracked separately; §15
    lists why each is irreducibly language-specific.
    """

    name: str = "toolchain"
    languages: frozenset[str] = frozenset()
    executable: str = ""
    env_var: str = ""
    #: Memoised result of the PATH lookup. A toolchain does not appear or vanish
    #: mid-run, and the registry consults ``available()`` once per *reference* --
    #: profiling an index of 288 files showed 29,818 ``shutil.which`` calls, each
    #: a directory scan, for 10% of total runtime. Sentinel is a bare object()
    #: because ``None`` is a meaningful answer here (not found).
    _located: Path | object | None = _UNPROBED

    def available(self) -> str:
        if self._locate() is None:
            return OracleAvailability.MISSING
        return OracleAvailability.DETECTED

    def version(self) -> str:
        located = self._locate()
        return str(located) if located else "missing"

    def _locate(self) -> Path | None:
        if self._located is not _UNPROBED:
            return self._located  # type: ignore[return-value]

        found: Path | None = None
        if self.env_var:
            root = os.environ.get(self.env_var)
            if root:
                candidate = Path(root) / "bin" / self.executable
                if candidate.exists():
                    found = candidate
        if found is None and self.executable:
            which = shutil.which(self.executable)
            found = Path(which) if which else None

        self._located = found
        return found


def default_oracles(project_root: Path) -> list[ResolverOracle]:
    """Every oracle this build knows about, in the order §7.2 prescribes."""
    return [
        JediOracle(project_root=project_root),
        ToolchainOracle(project_root=project_root, name="javac",
                        languages=frozenset({"java"}),
                        executable="javac", env_var="JAVA_HOME"),
        ToolchainOracle(project_root=project_root, name="tsserver",
                        languages=frozenset({"typescript", "javascript"}),
                        executable="tsc"),
        ToolchainOracle(project_root=project_root, name="gopls",
                        languages=frozenset({"go"}), executable="gopls"),
        ToolchainOracle(project_root=project_root, name="rust-analyzer",
                        languages=frozenset({"rust"}), executable="rust-analyzer"),
        ToolchainOracle(project_root=project_root, name="roslyn",
                        languages=frozenset({"csharp"}), executable="dotnet"),
    ]


class OracleRegistry:
    """Dispatches a reference to whichever oracle covers its language."""

    def __init__(
        self,
        oracles: Iterable[ResolverOracle] | None = None,
        *,
        project_root: Path | None = None,
        lang_of_file: dict[str, str] | None = None,
    ) -> None:
        root = project_root or Path()
        self._oracles = list(oracles) if oracles is not None else default_oracles(root)
        #: ``{file_path: lang}``, so a reference can be routed without the
        #: resolver having to carry language on every record.
        self._lang_of_file = lang_of_file or {}
        self._by_lang: dict[str, list[ResolverOracle]] = {}
        for oracle in self._oracles:
            for lang in oracle.languages:
                self._by_lang.setdefault(lang, []).append(oracle)

    def resolve(self, ref: UnresolvedRef) -> ResolvedTarget | None:
        lang = self._lang_of_file.get(ref.file_path)
        if lang is None:
            return None
        for oracle in self._by_lang.get(lang, ()):
            if oracle.available() == OracleAvailability.MISSING:
                continue
            answer = oracle.resolve(ref)
            if answer is not None:
                return answer
        return None

    def availability(self) -> dict[str, dict[str, str]]:
        """``{lang: {oracle: availability}}`` for the capability matrix (§12).

        Reported per install, not per release: whether Java has type-accurate
        resolution depends on whether a JDK is present on *this* machine, and an
        agent reading the matrix needs to know that before trusting a negative
        answer about Java.
        """
        matrix: dict[str, dict[str, str]] = {}
        for lang, oracles in sorted(self._by_lang.items()):
            matrix[lang] = {
                oracle.name: oracle.available()
                for oracle in sorted(oracles, key=lambda o: o.name)
            }
        return matrix

    def languages_without_tier_b(self) -> list[str]:
        """Languages where no oracle is available -- §7.2 step 4 applies."""
        missing: list[str] = []
        for lang, oracles in sorted(self._by_lang.items()):
            if all(o.available() == OracleAvailability.MISSING for o in oracles):
                missing.append(lang)
        return missing
