FROM python:3.11-slim

# ── Build-time deps ──────────────────────────────────────────────────────────
# libpq-dev  — psycopg2-binary needs the Postgres C client headers
# curl       — ECS container health check
# build-essential — native extension builds (e.g. hdbscan, umap-learn)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python env ───────────────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# ── Non-root user ─────────────────────────────────────────────────────────────
# ECS best-practice: never run as root inside the container.
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# ── Dependencies (cached layer) ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 8000

# ── Process manager ───────────────────────────────────────────────────────────
# gunicorn wraps uvicorn workers so the OS-level process manager can restart
# individual workers without killing the whole container.
# Workers = 1: snapshot jobs are long-running async tasks; multiple workers
# would each hold their own in-memory _jobs dict, causing /stream to poll the
# wrong worker's state. Keep 1 worker and let ECS task count handle scale.
CMD ["sh", "-c", \
     "gunicorn main:app \
      --worker-class uvicorn.workers.UvicornWorker \
      --workers 1 \
      --bind 0.0.0.0:${PORT:-8000} \
      --timeout 0 \
      --graceful-timeout 30 \
      --keep-alive 5 \
      --access-logfile - \
      --error-logfile -"]
