"""Java Analysis Bridge for LineageLens.

Locates or builds lineagelens-java.jar and executes the Java AST analyzer CLI.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Base path to src/java directory relative to this package file
JAVA_DIR = Path(__file__).resolve().parent.parent / "java"
JAR_PATH = JAVA_DIR / "build" / "libs" / "lineagelens-java.jar"


def find_java_executable() -> str:
    """Find system java binary."""
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        java_bin = Path(java_home) / "bin" / "java"
        if java_bin.exists():
            return str(java_bin)

    return "java"


def ensure_jar_built() -> Path:
    """Ensure lineagelens-java.jar exists, building it via Gradle if necessary."""
    if JAR_PATH.exists():
        return JAR_PATH

    logger.info("lineagelens-java.jar not found. Building Java component via Gradle...")
    gradlew = JAVA_DIR / "gradlew"
    cmd = [str(gradlew), "shadowJar"] if gradlew.exists() else ["gradle", "shadowJar"]

    try:
        res = subprocess.run(cmd, cwd=JAVA_DIR, capture_output=True, text=True, check=True)
        logger.info("Successfully built lineagelens-java.jar")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise RuntimeError(
            f"Failed to build Java analyzer component: {e}\n"
            f"Please run './gradlew shadowJar' inside {JAVA_DIR}"
        ) from e

    if not JAR_PATH.exists():
        raise RuntimeError(f"Build succeeded but JAR file not found at {JAR_PATH}")

    return JAR_PATH


def run_java_analysis(project_root: Path, output_file: Path | None = None) -> Path:
    """Run Java code graph analysis using lineagelens-java.jar.

    Args:
        project_root: Path to Java project
        output_file: Target output JSON file (default: project_root/.lineagelens/graph.json)

    Returns:
        Path to generated graph.json
    """
    java_bin = find_java_executable()
    jar_file = ensure_jar_built()

    if output_file is None:
        output_file = project_root / ".lineagelens" / "graph.json"

    output_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        java_bin,
        "-jar",
        str(jar_file),
        "analyze",
        str(project_root),
        "--output",
        str(output_file),
    ]

    logger.info("Executing Java AST Analysis: %s", " ".join(cmd))

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error("Java analysis error: %s", res.stderr)
        raise RuntimeError(f"Java analysis failed with exit code {res.returncode}:\n{res.stderr}")

    return output_file
