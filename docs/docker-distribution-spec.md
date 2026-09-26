# LineageLens Docker Distribution Spec

## Objective
To provide a self-contained, pre-configured Docker image for the LineageLens CLI. This "all-in-one" image bundles LineageLens along with all required language runtimes (Python, Node.js, Java, Go, Ruby, Rust, C/C++) and SCIP indexers for all supported languages (`scip-java`, `scip-typescript`, `scip-go`, `scip-python`, `scip-ruby`, `rust-analyzer`, `scip-clang`). This allows developers and CI/CD pipelines to execute deterministic graph extraction and queries securely, without polluting the host machine with complex compiler dependencies.

## Architecture: The "All-in-One" Toolchain Container
While the PyInstaller + Native Installer approach works well for desktop users, enterprise servers and CI/CD environments strongly prefer containerized execution. 

The `lineagelens/cli` Docker image will contain the entire extraction ecosystem:
1. **Python 3.10+**: The core runtime for LineageLens and `scip-python`.
2. **Node.js (LTS)**: Required to run `@sourcegraph/scip-typescript`.
3. **Java (OpenJDK 17+)**: Required to run `scip-java` (supports Java, Kotlin, Scala).
4. **Go**: Required to run `scip-go`.
5. **Ruby**: Required to run `scip-ruby`.
6. **Rust Toolchain (`rustup`)**: Required for `rust-analyzer` (which emits LSIF/SCIP).
7. **C/C++ Build Tools (LLVM/Clang)**: Required to support `scip-clang`.
8. **LineageLens Core**: Installed via standard Python packaging.
9. **Tree-sitter Grammars**: Pre-compiled and bundled inside the Python environment.

By bundling all compiler toolchains alongside the SCIP indexers, LineageLens can guarantee that it can generate high-fidelity (Tier B) ASTs for any language in any environment, fully isolated from the host OS.

> [!TIP]
> **Image Size Optimization:** To prevent the monolithic image from exceeding 5GB+ in size, this specification proposes publishing **language-specific image variants** (e.g., `lineagelens/cli:node`, `lineagelens/cli:java`) in addition to the `latest` multi-language monolith. This guarantees fast pull times for single-language repositories.

---

## Dockerfile Design

The Dockerfile uses a unified runtime base to ensure all indexers execute correctly:

1. **Base Image**: A robust base like `ubuntu:22.04` or `debian:bullseye-slim`.
2. **System Dependencies**: `apt-get` is used to install `openjdk-17-jdk`, `curl`, and `git`.
3. **Node.js**: Installed via NVM or NodeSource setup scripts.
4. **SCIP Toolchains**:
   - **TypeScript/JavaScript**: `npm install -g @sourcegraph/scip-typescript`
   - **Java/Kotlin/Scala**: `curl -fL https://github.com/coursier/coursier/releases/latest/download/cs-x86_64-pc-linux.gz | gzip -d > cs && ./cs install scip-java`
   - **Go**: `go install github.com/sourcegraph/scip-go/cmd/scip-go@latest`
   - **Python**: `pip install scip-python`
   - **Ruby**: `gem install scip-ruby`
   - **Rust**: `rustup component add rust-analyzer`
   - **C/C++**: Download and extract the latest `scip-clang` binary release from GitHub.
5. **LineageLens CLI**: Installed via `pip install .` directly from the repository source.
6. **Entrypoint**: The entrypoint is left as `/bin/bash` by default so users can chain `scip` and `lineagelens` commands, but `lineagelens` is added to the system `$PATH`.

---

## Instructions for Use (Customer-Facing Guide)

To use the containerized LineageLens on a local repository, you mount your source code into the container and execute commands exactly as you would natively.

### 1. Generating a Complete Index (with SCIP)
Because the container includes all SCIP tools, you can trigger full-fidelity (Tier B) indexing seamlessly by mapping your local directory (`$(pwd)`) to the container's `/workspace`.

```bash
docker run --rm \
  -v $(pwd):/workspace \
  -w /workspace \
  registry.contextserve.ai/lineagelens/cli:latest \
  bash -c "scip-typescript index && lineagelens index ."
```
*This command runs the SCIP indexer for TypeScript, drops the `index.scip` file in your local directory, and then immediately runs the LineageLens indexer to build the final graph.*

### 2. Querying the Graph
Once the graph database is generated in your local `.lineagelens` directory, you can query it interactively using the container:

```bash
docker run --rm \
  -v $(pwd):/workspace \
  -w /workspace \
  registry.contextserve.ai/lineagelens/cli:latest \
  lineagelens query . explain src/backend/Controller.java src/backend/Service.java
```

### 3. Resolving Enterprise Edge Cases

When deploying this container into real-world enterprise environments, several critical edge cases must be addressed to ensure SCIP indexers don't fail when attempting to compile the target project.

#### A. Cross-OS Native Bindings (Dependency Shadowing)
**The Problem:** If a developer on an Apple Silicon Mac mounts their `node_modules` into the Linux Docker container, native macOS binaries (like `esbuild` or `node-sass`) will crash the Linux container with an `Exec format error`.
**The Solution:** Resolve dependencies *inside* the container using a Docker anonymous volume to shadow the local directory. This ensures Linux binaries are compiled without overwriting the developer's macOS files:
```bash
docker run --rm \
  -v $(pwd):/workspace \
  -v /workspace/node_modules \
  -w /workspace \
  registry.contextserve.ai/lineagelens/cli:node \
  bash -c "npm install && scip-typescript index && lineagelens index ."
```

#### B. Private Packages and Authentication
**The Problem:** SCIP indexers invoke compilers (`npm`, `mvn`, `go build`). If a project relies on private registries (Artifactory, private NPM), the container will fail because it lacks the developer's credentials.
**The Solution:** Mount the developer's authentication files as read-only volumes into the container at runtime:
```bash
docker run --rm \
  -v $(pwd):/workspace \
  -v ~/.npmrc:/root/.npmrc:ro \
  -v ~/.ssh:/root/.ssh:ro \
  -w /workspace \
  registry.contextserve.ai/lineagelens/cli:node \
  bash -c "npm install && scip-typescript index"
```

#### C. File Ownership on Linux
**The Problem:** Docker runs as `root` by default. When the container generates `index.scip` and `.lineagelens/` into the mounted host directory, those files are owned by `root`, preventing the Linux developer from modifying or deleting them without `sudo`.
**The Solution:** Pass the host user's UID and GID to the container using the `--user` flag:
```bash
docker run --rm --user $(id -u):$(id -g) -v $(pwd):/workspace ...
```

#### D. Multi-Language Repository Indexing
**The Problem:** A repository like `bizbox-whatsapp` contains Java, TypeScript, Bash, and Docker. If an enterprise uses the slim, language-specific images (`:node`, `:java`) instead of the monolith for speed, how are all languages indexed into a single graph?
**The Solution:** There are two supported patterns for multi-language repos:

**Pattern 1: The Monolith Image** (Simplest configuration)
Use the `latest` multi-language image and chain all the SCIP commands in a single run:
```bash
docker run --rm -v $(pwd):/workspace registry.contextserve.ai/lineagelens/cli:latest \
  bash -c "scip-java index && npm install && scip-typescript index && lineagelens index ."
```

**Pattern 2: Multi-Stage Pipelines** (Best for CI/CD speed and caching)
Run a sequence of slim, language-specific containers that share the same workspace volume. Each SCIP container outputs its index to a uniquely named file, and LineageLens aggregates them at the end:
1. **Node Step (`:node` image):** `scip-typescript index -o index-ts.scip`
2. **Java Step (`:java` image):** `scip-java index -o index-java.scip`
3. **Merge Step (`:core` image):** `lineagelens index .` *(LineageLens automatically discovers all `*.scip` files in the workspace and stitches them together, falling back to its embedded Tree-sitter parsers for languages like Bash and Docker).*

### 4. CI/CD Integration
This container acts as the perfect drop-in execution environment for CI pipelines (GitHub Actions, GitLab CI, CircleCI), ensuring that graph generation is deterministic and doesn't require setting up Java, Node, and Python on the CI runner.

**GitHub Actions Example:**
```yaml
jobs:
  build-code-graph:
    runs-on: ubuntu-latest
    container: registry.contextserve.ai/lineagelens/cli:latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Generate SCIP & LineageLens Index
        run: |
          scip-typescript index
          lineagelens index .
          
      - name: Verify Determinism
        run: lineagelens verify .
```
