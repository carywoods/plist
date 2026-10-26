# plist operations control — container image
#
# The image is the RUNTIME only. The application code and the records it writes
# live in the git checkout inside the /data volume, because every record IS a
# file in this repository (lifecycle folders + ops-data/). The entrypoint clones
# or fast-forwards that checkout, then hands off to gunicorn.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GIT_TERMINAL_PROMPT=0 \
    TZ=America/Indiana/Indianapolis \
    PLIST_REPO_DIR=/data/plist

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git openssh-client tzdata \
 && rm -rf /var/lib/apt/lists/*

RUN pip install "Flask>=3.0,<4" "gunicorn>=21,<24"

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# One worker, one thread on purpose: record writes serialize in-process and
# next_ic.json has no cross-process lock, so concurrency must stay at one.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "1", \
     "--timeout", "120", "--graceful-timeout", "30", "--access-logfile", "-", "wsgi:app"]
