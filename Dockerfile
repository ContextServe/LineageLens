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
RUN pip install --no-cache-dir ".[mcp]"

RUN useradd -m -u 1000 lineagelens \
  && mkdir -p /workspace \
  && chown -R lineagelens:lineagelens /workspace

# We do not switch to the user yet, as subsequent targets need root to install packages.

# Mount a repository at /workspace and index it.
VOLUME ["/workspace"]
WORKDIR /workspace

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD lineagelens ontology --json > /dev/null || exit 1

ENV LINEAGELENS_PROJECT=/workspace
CMD ["lineagelens", "mcp", "/workspace"]


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
RUN apt-get update && apt-get install -y --no-install-recommends default-jdk \
  && curl -L https://github.com/scip-code/scip-java/releases/download/v0.13.1/scip-java-v0.13.1 -o /usr/local/bin/scip-java \
  && chmod +x /usr/local/bin/scip-java \
  && rm -rf /var/lib/apt/lists/*
USER lineagelens


# ==========================================
# GO TARGET
# ==========================================
FROM base AS go
RUN ARCH=$(uname -m) \
  && GO_ARCH=$(if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then echo "arm64"; else echo "amd64"; fi) \
  && curl -L "https://go.dev/dl/go1.21.1.linux-${GO_ARCH}.tar.gz" | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:${PATH}"
RUN go install github.com/scip-code/scip-go/cmd/scip-go@latest \
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
  && ARCH=$(uname -m) \
  && if [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then \
       curl -L https://github.com/sourcegraph/scip-clang/releases/latest/download/scip-clang-x86_64-linux -o /usr/local/bin/scip-clang \
       && chmod +x /usr/local/bin/scip-clang ; \
     else \
       echo "scip-clang prebuilt binary unavailable for $ARCH, skipping scip-clang install" ; \
     fi \
  && rm -rf /var/lib/apt/lists/*
USER lineagelens


# ==========================================
# MONOLITH TARGET (LATEST)
# ==========================================
FROM python:3.12-slim AS monolith
WORKDIR /app

# 1. Install base utilities & system dependencies
RUN apt-get update \
  && apt-get install -y --no-install-recommends git curl ca-certificates gnupg \
  && rm -rf /var/lib/apt/lists/*

# 2. System packages & Node.js
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
  && apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    default-jdk \
    ruby-full \
    build-essential \
    clang \
    llvm \
  && rm -rf /var/lib/apt/lists/*

# 3. TypeScript SCIP
RUN npm install -g @sourcegraph/scip-typescript

# 4. Java SCIP
RUN curl -L https://github.com/scip-code/scip-java/releases/download/v0.13.1/scip-java-v0.13.1 -o /usr/local/bin/scip-java \
  && chmod +x /usr/local/bin/scip-java

# 5. Go & Go SCIP (Support arm64 and amd64)
RUN ARCH=$(uname -m) \
  && GO_ARCH=$(if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then echo "arm64"; else echo "amd64"; fi) \
  && curl -L "https://go.dev/dl/go1.21.1.linux-${GO_ARCH}.tar.gz" | tar -C /usr/local -xz \
  && /usr/local/go/bin/go install github.com/scip-code/scip-go/cmd/scip-go@latest \
  && mv /root/go/bin/scip-go /usr/local/bin/ \
  && rm -rf /root/go

# 6. Ruby SCIP
RUN gem install scip-ruby

# 7. Rust & Analyzer
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
  && /root/.cargo/bin/rustup component add rust-analyzer \
  && cp /root/.cargo/bin/rust-analyzer /usr/local/bin/ \
  && rm -rf /root/.cargo/registry

# 8. Clang SCIP (x86_64 architecture fallback check)
RUN ARCH=$(uname -m) \
  && if [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then \
       curl -L https://github.com/sourcegraph/scip-clang/releases/latest/download/scip-clang-x86_64-linux -o /usr/local/bin/scip-clang \
       && chmod +x /usr/local/bin/scip-clang ; \
     else \
       echo "scip-clang prebuilt binary unavailable for $ARCH, skipping scip-clang install" ; \
     fi

ENV PATH="/usr/local/go/bin:${PATH}"

# 9. Copy project definition & install LineageLens python dependencies
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[mcp]"

RUN useradd -m -u 1000 lineagelens \
  && mkdir -p /workspace \
  && chown -R lineagelens:lineagelens /workspace

VOLUME ["/workspace"]
WORKDIR /workspace

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD lineagelens ontology --json > /dev/null || exit 1

ENV LINEAGELENS_PROJECT=/workspace
USER lineagelens
CMD ["lineagelens", "mcp", "/workspace"]


