# ─── Backend Dockerfile (Optimized) ─────────────────────
# Multi-stage: deps → production
FROM python:3.12-slim AS deps

WORKDIR /app
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn .

# ─── Production ─────────────────────────────────────────
FROM python:3.12-slim AS production

WORKDIR /app
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=deps /usr/local/bin/ /usr/local/bin/

# Copy application code
COPY . .

# Non-root user
RUN useradd --create-home clawith && \
    mkdir -p /data/agents && \
    chown -R clawith:clawith /app /data
USER clawith

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
