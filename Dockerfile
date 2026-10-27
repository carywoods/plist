# plist operations control — container image
#
# Two things live in two places on purpose:
#   * the APPLICATION CODE is baked into this image at /app (one image == one
#     version of the code)
#   * the RECORDS and the git checkout live in the /data volume (mutable; every
#     record IS a file in the repository, and every write is a git commit)
#
# A code change therefore ships by rebuilding this image; a record change ships
# as a git commit inside the volume. The entrypoint prepares the checkout in
# /data, then execs gunicorn from /app with OPS_REPO_ROOT pointing at it.
#
#   docker build -t plist-ops .
#   docker run --rm -p 8000:8000 -v plist-data:/data \
#     -e OPS_PASSWORD=... -e OPS_SECRET_KEY=... plist-ops
#
# Every OPS_* variable is documented in DEPLOY.md.

# ---------------------------------------------------------------- build stage
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY pyproject.toml ./
COPY opsapp ./opsapp

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install .

# -------------------------------------------------------------- runtime stage
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    GIT_TERMINAL_PROMPT=0 \
    TZ=America/Indiana/Indianapolis \
    PATH=/opt/venv/bin:$PATH \
    PLIST_REPO_DIR=/data/plist \
    OPS_REPO_ROOT=/data/plist

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git gosu openssh-client tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin plist

# Dependencies from the build stage; application code from the build context.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY wsgi.py ./
COPY opsapp ./opsapp
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# /app is owned by the runtime user. The entrypoint re-owns /data at start
# because a freshly mounted volume arrives owned by root.
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh \
 && chown -R plist:plist /app \
 && mkdir -p /data \
 && chown plist:plist /data

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# The entrypoint starts as root only to fix volume ownership and prepare the
# git checkout, then drops to the unprivileged 'plist' user for gunicorn.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# One worker, one thread on purpose: record writes serialize in-process and
# next_ic.json has no cross-process lock, so concurrency must stay at one.
CMD ["gunicorn", "--chdir", "/app", "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--threads", "1", \
     "--timeout", "120", "--graceful-timeout", "30", "--access-logfile", "-", \
     "wsgi:app"]
