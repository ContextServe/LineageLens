"""Auto-detect project configuration during `lineagelens init`.

Scans the project directory to infer sensible defaults for:
- source_roots: where Python packages live
- test_roots: where test packages/files live
- frameworks: what frameworks are imported or listed as dependencies
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import NamedTuple


class DetectionResult(NamedTuple):
    """Result of auto-detection scan."""
    source_roots: list[str]
    test_roots: list[str]
    frameworks: list[str]
    unsupported_frameworks: list[str]
    notes: list[str]


def detect_config(project: Path) -> DetectionResult:
    """Scan project and auto-detect configuration.

    Returns:
        DetectionResult with inferred roots and frameworks
    """
    source_roots = _detect_source_roots(project)
    test_roots = _detect_test_roots(project)
    frameworks, unsupported = _detect_frameworks(project)
    notes = _build_notes(source_roots, test_roots, frameworks, unsupported)

    return DetectionResult(
        source_roots=source_roots,
        test_roots=test_roots,
        frameworks=frameworks,
        unsupported_frameworks=unsupported,
        notes=notes,
    )


def is_java_project(project: Path) -> bool:
    """Check if the directory is a Java project (pom.xml, build.gradle, or .java files)."""
    return (
        (project / "pom.xml").exists()
        or (project / "build.gradle").exists()
        or (project / "build.gradle.kts").exists()
        or any(project.glob("src/main/java/**/*.java"))
        or any(project.glob("**/*.java"))
    )


def is_js_project(project: Path) -> bool:
    """Check if the directory is a JS/TS project (package.json, tsconfig.json, or .js/.ts files)."""
    return (
        (project / "package.json").exists()
        or (project / "tsconfig.json").exists()
        or any(project.glob("src/**/*.ts"))
        or any(project.glob("src/**/*.js"))
        or any(project.glob("src/**/*.tsx"))
        or any(project.glob("src/**/*.jsx"))
    )


def _detect_source_roots(project: Path) -> list[str]:
    """Find directories containing Python or Java packages/modules."""
    candidates = []

    # Check for Java conventional directories first
    if (project / "src" / "main" / "java").is_dir():
        candidates.append("src/main/java")

    # Check for common conventional directories
    for name in ["src", "lib", "app", "source"]:
        dir_path = project / name
        if dir_path.is_dir() and (_has_python_files(dir_path) or _has_java_files(dir_path)):
            candidates.append(name)

    # Check for top-level __init__.py (single-module layout)
    if (project / "__init__.py").exists():
        candidates.insert(0, ".")

    # Check for Python packages in root (e.g., a package_name/ dir)
    root_packages = [
        d.name for d in project.iterdir()
        if d.is_dir() and not d.name.startswith(".")
        and d.name not in ["tests", "test", "docs", "build", "dist", ".git", "__pycache__", "node_modules"]
        and (d / "__init__.py").exists()
    ]
    if root_packages:
        candidates.extend(root_packages)

    # Default if nothing found
    if not candidates:
        candidates = ["src"] if (project / "src").is_dir() else ["."]

    return list(dict.fromkeys(candidates))  # Remove duplicates, preserve order


def _detect_test_roots(project: Path) -> list[str]:
    """Find directories containing tests."""
    candidates = []

    for name in ["tests", "test", "spec", "testing"]:
        dir_path = project / name
        if dir_path.is_dir():
            candidates.append(name)

    # Look for test files in root (pytest convention)
    if any(p.name.startswith("test_") and p.suffix == ".py" for p in project.glob("test_*.py")):
        candidates.insert(0, ".")

    return list(dict.fromkeys(candidates))


def _detect_frameworks(project: Path) -> tuple[list[str], list[str]]:
    """Detect frameworks from imports and dependencies.

    Returns:
        (supported_frameworks, unsupported_frameworks) tuples
    """
    # Frameworks we recognize (by import name, not package name)
    KNOWN_FRAMEWORKS = {
        "fastapi", "flask", "django", "starlette",
        "typer", "click", "argparse",
        "pydantic", "dataclasses",
        "sqlalchemy", "tortoise", "orm",
        "pytest", "unittest", "nose",
        "celery", "rq", "dramatiq",
        "asyncio", "trio", "anyio",
        "strawberry", "graphene",
    }

    # Common utilities/libraries that aren't frameworks
    COMMON_UTILITIES = {
        "yaml", "pyyaml", "tomli", "tomllib", "toml",
        "httpx", "requests", "urllib3",
        "pytest", "unittest2",
    }

    # Internal project modules to ignore (inferred from source roots)
    internal_modules = _get_internal_modules(project)

    detected = set()

    # Scan imports in all Python files
    detected.update(_scan_imports(project))

    # Check pyproject.toml dependencies
    detected.update(_scan_dependencies(project))

    # Filter out internal modules and common utilities
    detected = {m for m in detected if m not in internal_modules and m.lower() not in COMMON_UTILITIES}

    # Separate known from unknown
    supported = [f for f in sorted(detected) if f in KNOWN_FRAMEWORKS]
    unsupported = [f for f in sorted(detected) if f not in KNOWN_FRAMEWORKS]

    return supported, unsupported


def _get_internal_modules(project: Path) -> set[str]:
    """Get internal project module names to exclude from framework detection."""
    internal = set()

    # Scan source roots for package names
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore

    pyproject = project / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text())
            # Get the package name
            pkg_name = data.get("project", {}).get("name", "").replace("-", "_")
            if pkg_name:
                internal.add(pkg_name)
            # Get packages from [tool.hatch.build.targets.wheel]
            packages = data.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {}).get("packages", [])
            for pkg_path in packages:
                # Convert "src/mypackage" to "mypackage"
                pkg_name = Path(pkg_path).name
                internal.add(pkg_name)
                # Also scan inside the package for submodules
                pkg_dir = project / pkg_path
                if pkg_dir.is_dir():
                    for item in pkg_dir.iterdir():
                        if item.is_file() and item.suffix == ".py" and item.name != "__init__.py":
                            internal.add(item.stem)
        except Exception:
            pass

    # Also check for top-level __init__.py or src/*/  pattern
    if (project / "__init__.py").exists():
        internal.add(project.name.replace("-", "_"))

    for src_root in ["src", "lib", "app"]:
        src_path = project / src_root
        if src_path.is_dir():
            for item in src_path.iterdir():
                if item.is_dir() and (item / "__init__.py").exists():
                    internal.add(item.name)
                    # Also add submodules
                    for subitem in item.iterdir():
                        if subitem.is_file() and subitem.suffix == ".py" and subitem.name != "__init__.py":
                            internal.add(subitem.stem)

    return internal


def _scan_imports(project: Path) -> set[str]:
    """Extract top-level module names from Python imports."""
    # Standard library modules to ignore
    STDLIB_MODULES = {
        "__future__", "__main__", "abc", "ast", "asyncio", "atexit", "argparse",
        "array", "base64", "binascii", "bisect", "builtins", "bz2", "calendar",
        "cmath", "cmd", "code", "codecs", "codeop", "collections", "colorsys",
        "concurrent", "configparser", "contextlib", "contextvars", "copy", "copyreg",
        "cProfile", "crypt", "csv", "ctypes", "curses", "dataclasses", "datetime",
        "dbm", "decimal", "difflib", "dis", "distutils", "doctest", "email",
        "encodings", "ensurepip", "enum", "errno", "faulthandler", "fcntl", "filecmp",
        "fileinput", "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
        "getpass", "gettext", "glob", "grp", "gzip", "hashlib", "heapq", "hmac",
        "html", "http", "idlelib", "imaplib", "imghdr", "imp", "importlib", "inspect",
        "io", "ipaddress", "itertools", "json", "keyword", "lib2to3", "linecache",
        "locale", "logging", "lzma", "mailbox", "mailcap", "marshal", "math",
        "mimetypes", "mmap", "modulefinder", "msilib", "msvcrt", "multiprocessing",
        "netrc", "nis", "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev",
        "parser", "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
        "platform", "plistlib", "poplib", "posix", "posixpath", "pprint", "profile",
        "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc", "queue", "quopri",
        "random", "readline", "reprlib", "resource", "rlcompleter", "runpy", "sched",
        "secrets", "select", "selectors", "shelve", "shlex", "shutil", "signal", "site",
        "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd", "sqlite3",
        "ssl", "stat", "statistics", "string", "stringprep", "struct", "subprocess",
        "sunau", "symbol", "symtable", "sys", "sysconfig", "syslog", "tabnanny",
        "tarfile", "telnetlib", "tempfile", "termios", "test", "textwrap", "threading",
        "time", "timeit", "tkinter", "token", "tokenize", "tomllib", "trace", "traceback",
        "tracemalloc", "tty", "turtle", "types", "typing", "typing_extensions", "unicodedata",
        "unittest", "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref",
        "webbrowser", "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc",
        "zipapp", "zipfile", "zipimport", "zlib",
    }

    imports = Counter()
    python_files = list(project.rglob("*.py"))[:100]  # Limit to first 100 files for speed

    for py_file in python_files:
        if any(part.startswith(".") for part in py_file.relative_to(project).parts):
            continue  # Skip hidden dirs

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_level = alias.name.split(".")[0]
                        if top_level not in STDLIB_MODULES:
                            imports[top_level] += 1
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top_level = node.module.split(".")[0]
                    if top_level not in STDLIB_MODULES:
                        imports[top_level] += 1
        except SyntaxError:
            pass

    # Return modules that appear multiple times (likely frameworks, not one-off imports)
    return {mod for mod, count in imports.items() if count >= 2 and not mod.startswith("_")}


def _scan_dependencies(project: Path) -> set[str]:
    """Extract package names from pyproject.toml or requirements.txt."""
    deps = set()

    # Check pyproject.toml
    pyproject = project / "pyproject.toml"
    if pyproject.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore

        try:
            data = tomllib.loads(pyproject.read_text())
            # Collect from [project] dependencies
            for dep in data.get("project", {}).get("dependencies", []):
                name = dep.split("[")[0].split(">=")[0].split("==")[0].split(">")[0].split("<")[0].strip()
                deps.add(name.replace("-", "_"))
        except Exception:
            pass

    # Check requirements.txt
    for req_file in ["requirements.txt", "requirements-dev.txt", "setup.py"]:
        req_path = project / req_file
        if req_path.exists():
            try:
                content = req_path.read_text()
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        name = line.split("[")[0].split(">=")[0].split("==")[0].split(">")[0].split("<")[0].strip()
                        deps.add(name.replace("-", "_"))
            except Exception:
                pass

    return deps


def _has_python_files(directory: Path) -> bool:
    """Check if directory or subdirectories contain Python files."""
    return any(directory.rglob("*.py"))


def _has_java_files(directory: Path) -> bool:
    """Check if directory or subdirectories contain Java files."""
    return any(directory.rglob("*.java"))


def _build_notes(
    source_roots: list[str],
    test_roots: list[str],
    frameworks: list[str],
    unsupported: list[str],
) -> list[str]:
    """Build human-readable notes about detection results."""
    notes = []

    if source_roots:
        notes.append(f"✓ Detected source roots: {', '.join(source_roots)}")
    else:
        notes.append("⚠ No source roots detected; defaulting to 'src'")

    if test_roots:
        notes.append(f"✓ Detected test roots: {', '.join(test_roots)}")
    else:
        notes.append("ℹ No test directories found (optional)")

    if frameworks:
        notes.append(f"✓ Detected frameworks: {', '.join(frameworks)}")
    else:
        notes.append("ℹ No known frameworks detected")

    if unsupported:
        notes.append(f"⚠ Also found unsupported frameworks: {', '.join(unsupported)}")
        notes.append("   You can add them to 'frameworks' in lineagelens.yaml if desired")

    return notes
