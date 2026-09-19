# LineageLens: indexer, query CLI and MCP server.
#
# No frontend stage and no web port: the React UI and the GraphQL/REST API were
# built on the schema-3 core and were removed with it. What ships is the CLI and
# the MCP server, which is what an agent actually talks to.

# ==========================================
# BASE TARGET
# ==========================================
FROM python:3.12-slim AS base
WORKDIR /app

# git so the image can index a repository it clones itself.
# curl and ca-certificates for downloading SCIP toolchains.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates gnupg \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# Tier A extraction needs no external toolchain.
# Install lineagelens and scip-python.
RUN pip install --no-cache-dir ".[mcp]" scip-python

RUN useradd -m -u 1000 lineagelens
# We do not switch to the user yet, as subsequent targets need root to install packages.

# Mount a repository at /work and index it.
VOLUME ["/work"]
WORKDIR /work

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD lineagelens ontology --json > /dev/null || exit 1

ENV LINEAGELENS_PROJECT=/work
CMD ["lineagelens", "mcp", "/work"]


# ==========================================
# NODE.JS TARGET
# ==========================================
FROM base AS node
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @sourcegraph/scip-typescript \
 && rm -rf /var/lib/apt/lists/*
USER lineagelens


# ==========================================
# JAVA TARGET
# ==========================================
FROM base AS java
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jdk \
 && curl -fL https://github.com/coursier/coursier/releases/latest/download/cs-x86_64-pc-linux.gz | gzip -d > /usr/local/bin/cs \
 && chmod +x /usr/local/bin/cs \
 && /usr/local/bin/cs install scip-java --install-dir /usr/local/bin \
 && rm -rf /var/lib/apt/lists/*
USER lineagelens


# ==========================================
# GO TARGET
# ==========================================
FROM base AS go
RUN curl -L https://go.dev/dl/go1.21.1.linux-amd64.tar.gz | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:${PATH}"
RUN go install github.com/sourcegraph/scip-go/cmd/scip-go@latest \
 && mv /root/go/bin/scip-go /usr/local/bin/ \
 && rm -rf /root/go
USER lineagelens


# ==========================================
# RUBY TARGET
# ==========================================
FROM base AS ruby
RUN apt-get update && apt-get install -y --no-install-recommends ruby-full build-essential \
 && gem install scip-ruby \
 && rm -rf /var/lib/apt/lists/*
USER lineagelens


# ==========================================
# RUST TARGET
# ==========================================
FROM base AS rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
RUN rustup component add rust-analyzer \
 && cp /root/.cargo/bin/rust-analyzer /usr/local/bin/ \
 && rm -rf /root/.cargo/registry
USER lineagelens


# ==========================================
# C/C++ TARGET (CLANG)
# ==========================================
FROM base AS clang
RUN apt-get update && apt-get install -y --no-install-recommends clang llvm \
 && curl -L https://github.com/sourcegraph/scip-clang/releases/latest/download/scip-clang-x86_64-linux -o /usr/local/bin/scip-clang \
 && chmod +x /usr/local/bin/scip-clang \
 && rm -rf /var/lib/apt/lists/*
USER lineagelens


# ==========================================
# MONOLITH TARGET (LATEST)
# ==========================================
FROM base AS monolith
# Install Node
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @sourcegraph/scip-typescript \
# Install Java
 && apt-get update && apt-get install -y --no-install-recommends openjdk-17-jdk \
 && curl -fL https://github.com/coursier/coursier/releases/latest/download/cs-x86_64-pc-linux.gz | gzip -d > /usr/local/bin/cs \
 && chmod +x /usr/local/bin/cs \
 && /usr/local/bin/cs install scip-java --install-dir /usr/local/bin \
# Install Go
 && curl -L https://go.dev/dl/go1.21.1.linux-amd64.tar.gz | tar -C /usr/local -xz \
 && /usr/local/go/bin/go install github.com/sourcegraph/scip-go/cmd/scip-go@latest \
 && mv /root/go/bin/scip-go /usr/local/bin/ \
 && rm -rf /root/go \
# Install Ruby
 && apt-get install -y --no-install-recommends ruby-full build-essential \
 && gem install scip-ruby \
# Install Rust
 && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
 && /root/.cargo/bin/rustup component add rust-analyzer \
 && cp /root/.cargo/bin/rust-analyzer /usr/local/bin/ \
 && rm -rf /root/.cargo/registry \
# Install C/C++
 && apt-get install -y --no-install-recommends clang llvm \
 && curl -L https://github.com/sourcegraph/scip-clang/releases/latest/download/scip-clang-x86_64-linux -o /usr/local/bin/scip-clang \
 && chmod +x /usr/local/bin/scip-clang \
# Cleanup
 && rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/local/go/bin:${PATH}"
USER lineagelens
