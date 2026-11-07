# CEL product API -- one service, one port.
#
# Build context is this directory (product_mvp/), and that is the whole point:
# Hugging Face Spaces builds a Docker Space from the root of the Space repo and
# offers no way to point at a parent directory. An earlier version of this file
# expected the repository root as its context (to `COPY agent_metabolism/`), which
# built fine on a laptop and failed on the Space with `agent_metabolism: not
# found` -- the same "works in exactly one layout" bug that v1.0.6 removed from
# cel_engine.py, reintroduced in the build file.
#
# So the core is fetched instead of copied: the release tarball for CEL_CORE_REF,
# pinned to a tag. That is the same bytes the release published, it cannot drift
# from the repository, and nothing is vendored twice.
#
#   cd product_mvp && docker compose up --build          # http://localhost:8000
#   cd product_mvp && docker build -t cel-console:0.3.0 .
#   docker run -p 8000:7860 -v cel-data:/data cel-console:0.3.0
#
# Hugging Face builds this file as-is: port 7860 is the Space runtime's default
# and is declared as the image default below.

FROM python:3.12-slim

# The core library, by release tag. Bump this line and the image follows the
# repository; leave it and the image stays reproducible.
ARG CEL_CORE_REF=v1.0.10

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CEL_CORE_PATH=/app/core

# 7860 is what a Hugging Face Docker Space routes to, and it is a *default*, not
# a fixed value: api_server.resolve_bind() reads PORT, then CEL_PORT, then this.
# Setting it here rather than on the command line keeps `docker run -e PORT=...`
# and a platform that injects PORT able to override it -- which is exactly the
# bug that made the Render health check read as dead while the log said "running".
# (The HEALTHCHECK below resolves the same way, for the same reason.)
ENV PORT=7860

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# The membrane and the penalty ledger.
#
# Default: the pinned release tarball, so a published image is reproducible and
# cannot drift from the repository. But the core is also *code under change*,
# and a service that calls a method the pinned core does not have would boot
# fine and fail on the first request -- the worst possible time to find out.
# So a build-context snapshot wins when present, and
# `scripts/deploy_demo.sh` puts one there when the working tree's core differs
# from the tag. See core_snapshot/README.md.
ADD https://github.com/liao-zhizhi/agent-metabolism-os/archive/refs/tags/${CEL_CORE_REF}.tar.gz /tmp/core.tar.gz
COPY core_snapshot /tmp/core_snapshot
RUN set -e; \
    mkdir -p /app/core; \
    if [ -f /tmp/core_snapshot/agent_metabolism/double_entry.py ]; then \
        cp -r /tmp/core_snapshot/agent_metabolism /app/core/agent_metabolism; \
        echo "core: working-tree snapshot (unreleased changes)"; \
    else \
        tar -xzf /tmp/core.tar.gz -C /app/core --strip-components=1; \
        echo "core: release ${CEL_CORE_REF}"; \
    fi; \
    rm -rf /tmp/core_snapshot /tmp/core.tar.gz; \
    test -f /app/core/agent_metabolism/double_entry.py; \
    test -f /app/core/agent_metabolism/penalty.py

# A non-root user, with the uid Hugging Face Spaces runs containers as. The docs
# are explicit ("the container runs with user ID 1000"), so any other uid risks
# permission errors on the directories the platform owns.
RUN useradd --create-home --uid 1000 cel

COPY --chown=cel:cel api_server.py cel_engine.py /app/product_mvp/
COPY --chown=cel:cel templates /app/product_mvp/templates
COPY --chown=cel:cel static /app/product_mvp/static

# The only writable path the service uses. 777 rather than a chown because on
# Space the platform may mount /data itself, and a mount keeps the owner it was
# mounted with -- a directory chowned in the image does not stay chowned through
# the mount. If it ends up unwritable anyway, the service refuses to boot and
# names both causes rather than failing at the first write.
RUN mkdir -p /data && chmod 777 /data

WORKDIR /app/product_mvp

USER cel

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.environ.get('CEL_PORT') or os.environ.get('PORT') or '7860'; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "api_server.py"]
