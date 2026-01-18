# syntax=docker/dockerfile:1

# =============================================================================
# Builder stage - install dependencies with uv
# =============================================================================
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

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
FROM python:3.14-slim-bookworm

WORKDIR /app

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

# Copy the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application files
COPY main.py utils.py firefly_client.py matcher.py ./
COPY templates/ ./templates/
COPY static/ ./static/

# Set ownership
RUN chown -R appuser:appuser /app

# Use the venv Python
ENV PATH="/app/.venv/bin:$PATH"

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# Run uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
