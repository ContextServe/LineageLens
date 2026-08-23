"""Module id construction.

The dotted module id is the join key between the graph and every external
inference result, so a wrong id does not degrade gracefully -- it silently
produces zero matches.
"""

from __future__ import annotations

from pathlib import Path

from lineagelens.analyzer import module_name
from lineagelens.config import ProjectConfig


def test_leading_source_root_is_stripped(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    config = ProjectConfig()
    assert module_name(tmp_path / "src" / "pkg" / "mod.py", tmp_path, config) == "pkg.mod"


def test_nested_src_component_is_preserved(tmp_path):
    """src/pkg/src/mod.py must be pkg.src.mod, not pkg.mod.

    Stripping every component equal to "src" collided with a real pkg/mod.py and
    disagreed with the dotted name any inference engine reports.
    """
    (tmp_path / "src" / "pkg" / "src").mkdir(parents=True)
    config = ProjectConfig()
    assert (
        module_name(tmp_path / "src" / "pkg" / "src" / "mod.py", tmp_path, config)
        == "pkg.src.mod"
    )


def test_source_root_that_is_itself_a_package_is_kept(tmp_path):
    """If src/ has an __init__.py it is part of the import path, so keep it."""
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "__init__.py").write_text("")
    config = ProjectConfig()
    assert module_name(tmp_path / "src" / "pkg" / "mod.py", tmp_path, config) == "src.pkg.mod"


def test_init_module_takes_the_package_name(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    config = ProjectConfig()
    assert module_name(tmp_path / "src" / "pkg" / "__init__.py", tmp_path, config) == "pkg"


def test_test_and_script_roots_keep_their_prefix(tmp_path):
    """is_test_path finds a test-root component in the dotted id; do not strip it."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts").mkdir()
    config = ProjectConfig()
    assert module_name(tmp_path / "tests" / "test_x.py", tmp_path, config) == "tests.test_x"
    assert module_name(tmp_path / "scripts" / "run.py", tmp_path, config) == "scripts.run"


def test_custom_source_root_is_honoured(tmp_path):
    (tmp_path / "app" / "pkg").mkdir(parents=True)
    config = ProjectConfig(source_roots=("app",))
    assert module_name(tmp_path / "app" / "pkg" / "mod.py", tmp_path, config) == "pkg.mod"


def test_bare_source_root_init_has_no_module_name(tmp_path):
    """A src/ that is not a package contributes nothing, so src/__init__.py is empty.

    The caller skips empty names; this must not raise.
    """
    (tmp_path / "src").mkdir()
    config = ProjectConfig()
    assert module_name(tmp_path / "src" / "__init__.py", tmp_path, config) == ""


def test_package_source_root_init_is_the_package(tmp_path):
    """If src/__init__.py really exists, src is a package and src/__init__.py is `src`."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    config = ProjectConfig()
    assert module_name(tmp_path / "src" / "__init__.py", tmp_path, config) == "src"
