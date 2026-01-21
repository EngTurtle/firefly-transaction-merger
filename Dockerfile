# syntax=docker/dockerfile:1

# =============================================================================
# Builder stage - install dependencies with uv
# =============================================================================
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app

# Enable bytecode compilation for faster startup
ENV UV_COMPILE_BYTECODE=1

# Copy dependency files first (better layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies into .venv
RUN uv sync --frozen --no-dev --no-install-project

# =============================================================================
# Final stage - minimal runtime image
# =============================================================================
FROM python:3.13-slim-bookworm AS runtime

WORKDIR /app

# Copy the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application files (excluding files in .dockerignore)
COPY . .

# Use the venv Python
ENV PATH="/app/.venv/bin:$PATH"

# Expose port
EXPOSE 8000

# Health check (uses Python since no shell in distroless)
HEALTHCHECK --interval=120s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/')"]

# Run uvicorn
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["main:app", "--host", "0.0.0.0", "--port", "8000"]
