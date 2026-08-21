# Contributing to LineageLens

Thank you for your interest in contributing! This guide covers development setup, testing, and our PR process.

## Development Setup

### 1. Clone and Create Branch

```bash
git clone https://github.com/lineagelens/lineagelens.git
cd lineagelens
git checkout -b feature/your-feature-name
```

### 2. Create Conda Environment

```bash
conda create -n lineagelens python=3.10
conda activate lineagelens
```

### 3. Install with Dev Dependencies

```bash
pip install -e ".[web,mcp,llm,dev]"
```

### 4. Verify Installation

```bash
lineagelens --help
lineagelens analyze .
pytest tests/ -v
```

## Development Workflow

### Making Changes

All changes should follow this pattern:

1. **If modifying query logic**: Add function to `src/lineagelens/queries.py`
2. **If adding API surface**: 
   - Add REST endpoint to `src/lineagelens/rest.py`, OR
   - Add GraphQL query to `src/lineagelens/web.py`, OR
   - Add MCP tool to `src/lineagelens/mcp_server.py`
3. **If changing data model**: Update `src/lineagelens/model.py`
4. **If changing analyzer logic**: Update `src/lineagelens/analyzer.py`
5. **Always add tests** alongside your changes

### Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test
pytest tests/test_analyzer.py::test_collects_entry_contract_and_argument_mapping -v

# With coverage
pytest tests/ --cov=src/lineagelens --cov-report=term-missing
```

**Test Coverage Target**: 80%+ for new code

### Linting

```bash
# Check code style
ruff check src/

# Format code
ruff format src/

# Type checking (if added)
mypy src/lineagelens/
```

### Manual Testing

```bash
# Analyze the repo itself
lineagelens analyze .
# Should report: 13 files, 141+ symbols, 0 failures

# Start web UI
lineagelens serve .
# Open http://localhost:8717

# Test MCP server
lineagelens-mcp
# Should start listening on stdin
```

## Code Style

### Python

- Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/)
- Use type hints for function parameters and returns
- Docstrings on public functions/classes
- Max line length: 120 characters

### Git Commits

- Concise, descriptive messages
- Format: `type(scope): description`
  - `feat(analyzer): add relative import handling`
  - `fix(queries): guard against None symbols`
  - `docs(readme): add quick start guide`
- End with: `Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>`

### Comments

- Explain WHY, not WHAT
- Link to GitHub issues/discussions when relevant
- Include examples for complex logic

## PR Process

### Before Opening PR

1. **Branch**: Off `main` or `develop` (not off old feature branches)
2. **Tests**: All tests pass, new tests added
3. **Linting**: `ruff check` and `ruff format` pass
4. **Documentation**: README updated if user-facing, ARCHITECTURE.md if technical
5. **Manual test**: `lineagelens analyze .` runs clean on this repo

### Opening PR

Use this template:

```markdown
## What Changed
Brief description of the feature/fix.

## Why
The problem it solves or motivation.

## How
Key implementation details or design decisions.

## Testing
How to verify this works (manual steps or test cases).

Fixes #<issue_number> (if applicable)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

### PR Review Checklist

Reviewers will check:
- ✅ Code follows style guidelines
- ✅ Tests cover the changes
- ✅ No breaking changes (or documented)
- ✅ Documentation is updated
- ✅ Works on LineageLens repo itself

### Merging

- Squash commits or rebase-and-merge (keep history clean)
- Delete branch after merge
- Update CHANGELOG.md if versioning has changed

## Key Principles

### 1. Single Source of Truth
When adding a query or operation:
- Implement once in `queries.py`
- Expose via REST, GraphQL, MCP (thin adapters, no logic duplication)

### 2. Resilient Analyzer
- Catch errors per-file, don't fail globally
- Log failures to `AnalysisReport`
- Test edge cases explicitly

### 3. Evidence is King
- Every signal must be evidence-labelled
- Tier 1: deterministic facts (AST)
- Tier 2: deterministic heuristics (static inference)
- Tier 3: probabilistic (LLM, always separate)

### 4. Configuration Over Hardcoding
- Risk rules, entry points, frameworks → configurable via `lineagelens.yaml`
- No hardcoded patterns (except defaults)

### 5. Tested by Default
- Changes to core logic must have tests
- At minimum: happy path + error case
- Use fixture graph from `test_queries.py` as template

## Directory Structure Reference

```
src/lineagelens/
├── __init__.py          # Version + exports
├── analyzer.py          # Core analysis engine (Definitions + Relationships visitors)
├── model.py             # Data model (Symbol, Relation, Container, Evidence)
├── config.py            # Configuration loading and validation
├── queries.py           # Single source of truth (all graph operations)
├── rest.py              # REST API endpoints (thin adapter over queries.py)
├── web.py               # GraphQL + static file serving
├── mcp_server.py        # MCP tools (thin adapter over queries.py)
├── cli.py               # Command-line interface
├── report.py            # Analysis reporting
└── enrich.py            # Optional LLM enrichment (Tier 3)

tests/
├── test_analyzer.py     # Analyzer correctness
├── test_queries.py      # Query correctness + fixture graph
├── fixtures.py          # Shared test fixtures

docs/
├── ARCHITECTURE.md      # Technical design (for contributors)
├── CONTRIBUTING.md      # This file
├── claude-mcp-setup.md  # User guide: Claude Code
└── deploy-chatgpt-actions.md  # User guide: ChatGPT
```

## Getting Help

- **Questions?** Open a [GitHub Discussion](https://github.com/lineagelens/lineagelens/discussions)
- **Found a bug?** Open a [GitHub Issue](https://github.com/lineagelens/lineagelens/issues)
- **Architecture questions?** See [ARCHITECTURE.md](ARCHITECTURE.md)
- **User setup issues?** See relevant guide in `docs/`

## License

By contributing, you agree that your contributions will be licensed under the project's license (see [LICENSE](../LICENSE)).

---

Thank you for helping make LineageLens better! 🎉
