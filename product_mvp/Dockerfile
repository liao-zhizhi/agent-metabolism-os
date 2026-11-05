# CEL product API -- one service, one port.
#
# Build context is this repository's root (the parent of product_mvp/), because
# the image vendors agent_metabolism rather than installing it from a registry:
# the product ships the same bytes the audit trail was produced with, and the
# core has no dependencies to resolve.
#
#   cd product_mvp && docker compose up --build
#   docker build -f product_mvp/Dockerfile -t cel-console .   # from the root

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CEL_CORE_PATH=/app

# No CEL_PORT on purpose: the entrypoint reads PORT first if the platform sets
# one (Render, Railway, Heroku, Fly all do) and falls back to 8000. Pinning the
# port here is how a Docker deploy dies its health check while logging "running".

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
COPY product_mvp/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# The membrane and the penalty ledger. Standard library only -- no install step,
# and CEL_CORE_PATH=/app is what lets cel_engine find it.
COPY agent_metabolism /app/agent_metabolism

# The service itself.
COPY product_mvp/api_server.py /app/product_mvp/api_server.py
COPY product_mvp/cel_engine.py /app/product_mvp/cel_engine.py
COPY product_mvp/templates /app/product_mvp/templates
COPY product_mvp/static /app/product_mvp/static

WORKDIR /app/product_mvp

# Not root: the service writes nothing except an optional append-only log.
RUN useradd --create-home --uid 10001 cel && mkdir -p /data && chown cel:cel /data
USER cel

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "api_server.py"]
