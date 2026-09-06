"""JavaScript / TypeScript Analysis Bridge for LineageLens.

Locates node and executes lineagelens-js dist/cli.js.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

JS_DIR = Path(__file__).resolve().parent.parent / "java" / ".." / "js"
JS_CLI_PATH = JS_DIR / "dist" / "cli.js"


def find_node_executable() -> str:
    """Find system node binary."""
    node_bin = shutil.which("node")
    if node_bin:
        return node_bin
    raise RuntimeError("node executable not found in PATH. Please install Node.js 18+")


def ensure_js_built() -> Path:
    """Ensure dist/cli.js exists, building it via npm if necessary."""
    if JS_CLI_PATH.exists():
        return JS_CLI_PATH

    logger.info("dist/cli.js not found. Building JS component via npm...")
    npm_bin = shutil.which("npm") or "npm"

    try:
        subprocess.run([npm_bin, "install"], cwd=JS_DIR, capture_output=True, text=True, check=True)
        subprocess.run([npm_bin, "run", "build"], cwd=JS_DIR, capture_output=True, text=True, check=True)
        logger.info("Successfully built lineagelens-js")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise RuntimeError(
            f"Failed to build JS analyzer component: {e}\n"
            f"Please run 'npm install && npm run build' inside {JS_DIR}"
        ) from e

    if not JS_CLI_PATH.exists():
        raise RuntimeError(f"Build succeeded but cli.js file not found at {JS_CLI_PATH}")

    return JS_CLI_PATH


def run_js_analysis(project_root: Path, output_file: Path | None = None) -> Path:
    """Run JavaScript/TypeScript code graph analysis using lineagelens-js.

    Args:
        project_root: Path to JS/TS project
        output_file: Target output JSON file (default: project_root/.lineagelens/graph.json)

    Returns:
        Path to generated graph.json
    """
    node_bin = find_node_executable()
    cli_js = ensure_js_built()

    if output_file is None:
        output_file = project_root / ".lineagelens" / "graph.json"

    output_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        node_bin,
        str(cli_js),
        "analyze",
        str(project_root),
        "--output",
        str(output_file),
    ]

    logger.info("Executing JS/TS AST Analysis: %s", " ".join(cmd))

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error("JS analysis error: %s", res.stderr)
        raise RuntimeError(f"JS analysis failed with exit code {res.returncode}:\n{res.stderr}")

    return output_file
