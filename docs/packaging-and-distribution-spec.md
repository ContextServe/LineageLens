# LineageLens Packaging & Distribution Spec

## Objective
To distribute LineageLens as a standalone executable wrapped in native platform installers (Windows and macOS). These installers will automate the installation of essential prerequisites (such as SCIP indexers and their underlying runtimes) so that end users do not need to manually configure Python, Node.js, or Java environments before indexing their codebases.

## Architecture & Toolchain

The distribution process relies on a two-phase build pipeline:
1. **Freezing** the Python application into a standalone binary.
2. **Packaging** the binary into native installers that perform prerequisite checks and installations.

---

### Phase 1: Standalone Binary Compilation
**Tool:** `PyInstaller`

The `lineagelens` application is a Python CLI. To remove the requirement for users to install Python and manage virtual environments, the application is compiled into a single binary (`lineagelens.exe` for Windows, `lineagelens` for macOS).

**Implementation Details:**
- **Command:** `pyinstaller --name lineagelens --onefile src/lineagelens/cli.py`
- **Asset Bundling:** The `tree-sitter` grammars (which are compiled `.so`/`.dylib`/`.dll` libraries) must be explicitly included in the PyInstaller `.spec` file's `datas` array. If they are missed, the runtime will fail to extract them.
- **Outcome:** A self-contained binary that embeds the Python interpreter and all Python dependencies (`PyYAML`, `httpx`, `tree-sitter`).

---

### Phase 2: Native Installers & Prerequisite Checks

Once the binaries are built, they must be wrapped in platform-specific installers that can reach out to the host OS, check for dependencies, and install external SCIP toolchains.

#### 1. Windows Installer (`.exe`)
**Tool:** Inno Setup

Inno Setup uses Pascal scripting to manage installation logic, allowing deep integration with the Windows Registry.

**Installer Responsibilities:**
1. **Binary Deployment:** Extract `lineagelens.exe` to `%PROGRAMFILES%\LineageLens` and add the directory to the System `PATH`.
2. **Pre-install Runtime Checks:** 
   - Check the Windows Registry (e.g., `HKLM\SOFTWARE\Node.js`) to verify Node.js is installed. If missing, silently download and execute the Node.js `.msi` installer.
   - Check for a valid Java Runtime Environment (required for `scip-java`).
3. **Post-install Toolchain Setup:** 
   - Execute a background terminal: `cmd.exe /c "npm install -g @sourcegraph/scip-typescript"`
   - Execute Coursier installation for `scip-java` if a Java environment is detected.

#### 2. macOS Installer (`.pkg`)
**Tool:** `pkgbuild` (Native Xcode packaging)

Apple's native `.pkg` format allows embedding standard Bash scripts (`preinstall` and `postinstall`) to handle system configuration.

**Installer Responsibilities:**
1. **Binary Deployment:** Deploy the compiled `lineagelens` binary to `/usr/local/bin/lineagelens`.
2. **Pre-install Runtime Checks (via `postinstall.sh`):**
   - Check `command -v node`. If missing, download and install Node via the official binary tarball or prompt the user.
   - Check `command -v java`. If missing, download an OpenJDK tarball or prompt the user.
3. **Post-install Toolchain Setup:**
   - Execute `npm install -g @sourcegraph/scip-typescript`.
   - Execute `cs install scip-java` (assuming `coursier` is installed).

*(Alternative macOS Option): Provide a `curl -fsSL https://contextserve.ai/install.sh | bash` script instead of a formal `.pkg`. This script can run the dependency checks and download the raw PyInstaller binary directly, which is increasingly common for developer tools.*

---

## CI/CD Pipeline Integration

To automate releases, the build process will be integrated into GitHub Actions (`.github/workflows/build-installers.yml`):

1. **Matrix Build:** Run concurrent jobs on `windows-latest` and `macos-latest`.
2. **Compilation Step:** Execute `PyInstaller` to generate the binaries.
3. **Packaging Step:** 
   - On Windows: Run `iscc setup.iss` (Inno Setup CLI) to generate the installer executable.
   - On macOS: Run `pkgbuild --root Payload --scripts scripts --identifier com.contextserve.lineagelens lineagelens-installer.pkg`
4. **Artifact Upload:** Attach the resulting Windows `.exe` installer and macOS `.pkg` installer to the GitHub Release.
