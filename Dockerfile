# ==============================================================================
# Multi-Stage Dockerfile for FieldTrack ERP (Coolify / Production Ready)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build virtual environment and compiled wheels
# ------------------------------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies needed for compilation (psycopg2, Pillow, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ------------------------------------------------------------------------------
# Stage 2: Production Lightweight Runtime Image
# ------------------------------------------------------------------------------
FROM python:3.13-slim AS runner

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    DJANGO_SETTINGS_MODULE=fieldtrack.settings

WORKDIR /app

# Install runtime libraries & tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libjpeg62-turbo \
    zlib1g \
    curl \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Copy python virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy project files
COPY . /app/

# Setup permissions & runtime directories
RUN chmod +x /app/docker-entrypoint.sh && \
    mkdir -p /app/staticfiles /app/media /app/data && \
    useradd -u 1000 -m appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Coolify / Docker Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/login/ || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
