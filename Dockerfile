# LineageLens: indexer, query CLI and MCP server.
#
# No frontend stage and no web port: the React UI and the GraphQL/REST API were
# built on the schema-3 core and were removed with it. What ships is the CLI and
# the MCP server, which is what an agent actually talks to.
FROM python:3.12-slim
WORKDIR /app

# git so the image can index a repository it clones itself.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# Tier A extraction needs no external toolchain: every tree-sitter grammar is a
# pinned core dependency. Java/Go/Rust/C# Tier B resolvers are system programs
# and are deliberately absent -- those languages index at Tier A, and the
# coverage envelope on every answer says so. Add a JDK here if you want
# type-accurate Java resolution in-container.
RUN pip install --no-cache-dir ".[mcp]"

RUN useradd -m -u 1000 lineagelens
USER lineagelens

# Mount a repository at /work and index it.
VOLUME ["/work"]
WORKDIR /work

# Fails if the package cannot load its runtime data -- extraction specs and
# contract adapters. A wheel missing those installs an engine with nothing to
# run, and every query would return empty without raising.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD lineagelens ontology --json > /dev/null || exit 1

ENV LINEAGELENS_PROJECT=/work
CMD ["lineagelens", "mcp", "/work"]
