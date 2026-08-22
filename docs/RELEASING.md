# Releasing LineageLens

This guide covers building and publishing releases to PyPI and other platforms.

## Prerequisites

- Python 3.10+ with `build` and `twine` installed: `pip install build twine`
- Node.js 20+ for building the frontend (if you modified `frontend/src/`)
- PyPI account with credentials configured (via `~/.pypirc` or environment variables)
- For marketplace publishes: GitHub account with release permissions, VS Code Marketplace publisher account, Docker Hub account (optional)

## Release Workflow

### 1. Prepare the Release

Increment the version in `pyproject.toml`:
```toml
[project]
version = "0.2.0"  # Bump from 0.1.0
```

### 2. Build the Frontend (if modified)

If you changed any code in `frontend/src/`, rebuild the distribution:

```bash
cd frontend
npm install
npm run build
cd ..
```

Commit the updated `frontend/dist/`:
```bash
git add frontend/dist
git commit -m "build: update frontend dist for v0.2.0"
```

If you did NOT modify the frontend, skip this step — the existing `frontend/dist` is already committed.

### 3. Build the Package

```bash
python -m build .
```

This creates:
- `dist/lineagelens-0.2.0.tar.gz` (source distribution)
- `dist/lineagelens-0.2.0-py3-none-any.whl` (wheel)

Both artifacts include the prebuilt `frontend/dist/` automatically (via `pyproject.toml`'s `force-include` config).

### 4. Test the Package Locally

In a fresh virtual environment, verify the wheel works:

```bash
python -m venv /tmp/lineagelens-test
source /tmp/lineagelens-test/bin/activate  # or `activate.bat` on Windows
pip install dist/lineagelens-0.2.0-py3-none-any.whl[web,mcp,llm]

# Verify it installs and works
lineagelens --help
lineagelens analyze /path/to/sample/project
lineagelens serve /path/to/sample/project
# Open http://127.0.0.1:8717 in browser — frontend should render
```

### 5. Publish to TestPyPI (Optional)

First test on the test repository to catch issues before the real publish:

```bash
twine upload --repository testpypi dist/*
```

Then test the installation:
```bash
pip install -i https://test.pypi.org/simple/ lineagelens[web,mcp,llm]
```

### 6. Publish to PyPI (Production)

When ready for the real release:

```bash
twine upload dist/*
```

Users can now install with:
```bash
pip install lineagelens[web]          # Web UI + REST API
pip install lineagelens[mcp]          # Claude Code integration
pip install lineagelens[web,mcp,llm]  # All features
```

### 7. Create a GitHub Release

Push the version bump commit and create a GitHub release:

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.2.0"
git tag v0.2.0
git push origin main --tags
```

Then on GitHub:
1. Go to Releases → Draft a new release
2. Select the `v0.2.0` tag
3. Title: `LineageLens 0.2.0`
4. Body (template):
   ```
   ## What's New
   - Brief description of features/fixes

   ## Installation
   \`\`\`
   pip install lineagelens[web,mcp,llm]
   \`\`\`

   ## Thanks
   All contributors and testers!
   ```
5. Upload `dist/lineagelens-0.2.0-py3-none-any.whl` as an asset (optional — users will use PyPI)
6. Publish

### 8. Publish Docker Image (Optional)

For ChatGPT Actions users who want a pre-built hosted version:

```bash
docker build -t lineagelens/lineagelens:0.2.0 .
docker tag lineagelens/lineagelens:0.2.0 lineagelens/lineagelens:latest
docker push lineagelens/lineagelens:0.2.0
docker push lineagelens/lineagelens:latest
```

Users can then deploy with:
```bash
docker run -p 8000:8000 lineagelens/lineagelens:latest
```

### 9. Publish VS Code Extension (Optional)

```bash
cd editors/vscode
npm install
npm run build
npm run package        # Creates lineagelens-vscode-0.2.0.vsix
vsce publish           # Requires VS Code Marketplace publisher account
cd ../..
```

## Troubleshooting

**"ModuleNotFoundError: No module named 'lineagelens'" after `pip install lineagelens-*.whl`**
- Verify the wheel was built correctly: `unzip -l dist/lineagelens-*.whl | grep lineagelens/`
- The wheel should contain `lineagelens/` directory with Python files

**"Frontend not found" when running `lineagelens serve .`**
- The wheel includes `lineagelens/frontend/dist/`, but `pip install` might be installing an older version
- Verify: `python -c "from lineagelens.web import locate_frontend_dist; print(locate_frontend_dist())"`
- If it's found, but still not rendering, check browser console for errors (F12)

**Docker build fails**
- Ensure `frontend/dist/` is committed and present at build time
- `docker build` from the repo root, not a subdirectory

## Version Numbering

Follow [Semantic Versioning](https://semver.org/):
- **MAJOR.MINOR.PATCH** (e.g., 0.2.0)
- Increment MAJOR for breaking API changes
- Increment MINOR for new features (backward compatible)
- Increment PATCH for bug fixes

For pre-release versions: `0.2.0rc1` or `0.2.0a1`

## Summary

To release a new version:
1. Update version in `pyproject.toml`
2. If frontend changed: `cd frontend && npm install && npm run build && cd .. && git add frontend/dist && git commit`
3. `python -m build .`
4. Test locally or on TestPyPI
5. `twine upload dist/*`
6. Create GitHub release with tag
7. (Optional) Build and push Docker image
8. (Optional) Publish VS Code extension

That's it! Users can now `pip install lineagelens[web]` and get the full package with prebuilt frontend, zero extra steps.
