"""Deployment-unit detection (issue #51 §7.4).

A monorepo with twelve services needs to know which one a symbol belongs to,
because it is what separates two different answers to "what does changing this
affect":

* **in-process** -- call and data edges inside one deployable. A compile or test
  failure, one deploy.
* **cross-service** -- reached through a contract node (§8). A wire-compatibility
  and deploy-ordering problem, which is a different conversation.

Schema 3 had no such dimension, so ``get_impact`` could not distinguish them.

Detection is by build manifest, because that is what actually defines a
deployable. A directory is a service if it declares dependencies and an
identity; nesting is resolved by taking the *nearest* enclosing manifest, so a
Maven module inside a parent POM is its own service.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import tomllib

from .core import Service, service_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ManifestKind:
    """A build manifest and how to read a name out of it."""

    filename: str
    kind: str


#: Ordered by specificity: a directory with both ``package.json`` and a
#: ``Dockerfile`` is an npm service that happens to be containerised, so the
#: language manifest wins over the packaging one.
MANIFESTS: tuple[ManifestKind, ...] = (
    ManifestKind("pom.xml", "maven"),
    ManifestKind("build.gradle", "gradle"),
    ManifestKind("build.gradle.kts", "gradle"),
    ManifestKind("package.json", "npm"),
    ManifestKind("pyproject.toml", "pypi"),
    ManifestKind("setup.py", "pypi"),
    ManifestKind("go.mod", "go"),
    ManifestKind("Cargo.toml", "cargo"),
    ManifestKind("Dockerfile", "docker"),
)

#: Directories never worth walking. Kept here rather than in the walker so
#: service detection and file discovery agree on what is vendored.
IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".lineagelens", ".codegraph", "graphify-out",
    "node_modules", "vendor", "target", "build", "dist", "out",
    ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".tox", ".gradle", ".idea", ".vscode", "bin", "obj",
    "site-packages", ".next", ".nuxt", "coverage", ".terraform",
})


def discover_services(root: Path) -> list[Service]:
    """Every deployment unit under ``root``, outermost first.

    The root itself is always a service even if it has no manifest: a plain
    directory of scripts is still one deployable, and every node needs a
    ``service_id``.
    """
    found: dict[str, Service] = {}

    root_service = _service_at(root, root) or Service(
        id=service_id(root.name or "root", "."),
        name=root.name or "root",
        root=".",
        kind="directory",
    )
    found[root_service.root] = root_service

    for path in _walk_dirs(root):
        service = _service_at(path, root)
        if service is not None and service.root not in found:
            found[service.root] = service

    return [found[key] for key in sorted(found)]


def _walk_dirs(root: Path) -> list[Path]:
    """Directories worth checking for a manifest, deterministically ordered."""
    out: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or entry.is_symlink():
                continue
            if entry.name in IGNORED_DIRS or entry.name.startswith("."):
                continue
            out.append(entry)
            stack.append(entry)
    return sorted(out)


def _service_at(path: Path, root: Path) -> Service | None:
    for manifest in MANIFESTS:
        candidate = path / manifest.filename
        if candidate.is_file():
            rel = _rel(path, root)
            return Service(
                id=service_id(_name_from(candidate, manifest.kind, path), rel),
                name=_name_from(candidate, manifest.kind, path),
                root=rel,
                kind=manifest.kind,
                manifest_path=_rel(candidate, root),
            )

    for entry in sorted(path.glob("*.csproj")):
        rel = _rel(path, root)
        return Service(
            id=service_id(entry.stem, rel), name=entry.stem, root=rel,
            kind="dotnet", manifest_path=_rel(entry, root),
        )
    return None


def _name_from(manifest: Path, kind: str, directory: Path) -> str:
    """Best-effort service name, falling back to the directory name.

    Parsing is deliberately shallow and failure-tolerant: the directory name is
    always a usable identity, so a malformed manifest degrades the label rather
    than failing the index.
    """
    fallback = directory.name or "root"
    try:
        if kind == "npm":
            data = json.loads(manifest.read_text("utf-8"))
            return str(data.get("name") or fallback).lstrip("@").replace("/", "-")
        if kind == "pypi" and manifest.name == "pyproject.toml":
            data = tomllib.loads(manifest.read_text("utf-8"))
            return str(
                data.get("project", {}).get("name")
                or data.get("tool", {}).get("poetry", {}).get("name")
                or fallback
            )
        if kind == "cargo":
            data = tomllib.loads(manifest.read_text("utf-8"))
            return str(data.get("package", {}).get("name") or fallback)
        if kind == "go":
            for line in manifest.read_text("utf-8").splitlines():
                if line.startswith("module "):
                    return line.removeprefix("module ").strip().rsplit("/", 1)[-1]
        if kind == "maven":
            # A regex rather than an XML parse: only the first artifactId is
            # wanted, and it is always before the first <dependencies> block.
            text = manifest.read_text("utf-8")
            head = text.split("<dependencies>", 1)[0]
            match = re.search(r"<artifactId>([^<]+)</artifactId>", head)
            if match:
                return match.group(1).strip()
    except (OSError, ValueError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        logger.debug("could not read a name from %s: %s", manifest, exc)
    return fallback


def _rel(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
    return rel or "."


class ServiceLocator:
    """Maps a file to its nearest enclosing service."""

    __slots__ = ("_roots",)

    def __init__(self, services: list[Service]) -> None:
        # Longest root first, so the nearest enclosing manifest wins: a Maven
        # module inside a parent POM must map to the module, not the parent.
        self._roots = sorted(
            ((s.root, s) for s in services),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )

    def for_path(self, rel_path: str) -> Service | None:
        posix = rel_path.replace("\\", "/")
        for root, service in self._roots:
            if root == ".":
                continue
            if posix == root or posix.startswith(f"{root}/"):
                return service
        return self._roots[-1][1] if self._roots else None
