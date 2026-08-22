# Single-stage build: Python backend with prebuilt frontend
FROM python:3.11-slim
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Copy prebuilt frontend (committed to git)
COPY frontend/dist ./frontend/dist

# Copy Python source
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# Install LineageLens with all optional dependencies
RUN pip install --no-cache-dir -e ".[web,mcp,llm]"

# Create non-root user
RUN useradd -m -u 1000 lineagelens
USER lineagelens

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8000/api/v1/entry-points')" || exit 1

# Default: serve the web UI
ENV HOST=0.0.0.0
ENV PORT=8000
EXPOSE 8000

CMD ["lineagelens", "serve", "."]
