#!/bin/sh
# plist container entrypoint.
#
# Two stores, on purpose:
#   /app        — the application code, baked into the image (read-only)
#   $DATA_DIR   — the git checkout (records + every write) and the deploy key
#                 that this container generated for itself
#
# The image carries the code, the volume carries the state. The entrypoint
# prepares the checkout, then drops root and execs gunicorn as the unprivileged
# 'plist' user with OPS_REPO_ROOT pointing at the checkout.
set -eu

REPO_URL="${OPS_GIT_REMOTE_URL:-https://github.com/carywoods/plist.git}"
PUSH_URL="${OPS_GIT_PUSH_URL:-git@github.com:carywoods/plist.git}"
BRANCH="${OPS_GIT_BRANCH:-main}"
DATA_DIR="${OPS_DATA_VOLUME:-/data}"
REPO_DIR="${PLIST_REPO_DIR:-$DATA_DIR/plist}"
SSH_DIR="$DATA_DIR/ssh"
KEY="$SSH_DIR/id_ed25519"
RUN_USER="${OPS_RUN_USER:-plist}"

mkdir -p "$DATA_DIR" "$SSH_DIR"
chmod 700 "$SSH_DIR"

if [ ! -f "$KEY" ]; then
  echo "plist: generating deploy key"
  ssh-keygen -q -t ed25519 -N "" -C "plist-coolify@$(hostname)" -f "$KEY"
  chmod 600 "$KEY"
fi
if [ -f "$KEY.pub" ]; then
  echo "plist: deploy public key (needs write access on the GitHub repo):"
  cat "$KEY.pub"
fi

export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$SSH_DIR/known_hosts"

# --system so the identity and safe.directory apply to the unprivileged user
# gunicorn runs as, not only to root.
git config --system user.name "${OPS_GIT_USER_NAME:-Dax-A}"
git config --system user.email "${OPS_GIT_USER_EMAIL:-herm@indybiosystems.com}"
git config --system --add safe.directory "$REPO_DIR"

# Read path: an explicit OPS_GIT_REMOTE_URL wins; otherwise use SSH once the
# deploy key is authorized (that keeps working if the repo goes private) and
# fall back to HTTPS while it is public and the key is not on the repo yet.
if [ -n "${OPS_GIT_REMOTE_URL:-}" ]; then
  FETCH_URL="$OPS_GIT_REMOTE_URL"
elif git ls-remote --exit-code "$PUSH_URL" >/dev/null 2>&1; then
  FETCH_URL="$PUSH_URL"
  echo "plist: deploy key can read $PUSH_URL — fetching over SSH"
else
  FETCH_URL="https://github.com/carywoods/plist.git"
  echo "plist: deploy key cannot read $PUSH_URL yet — falling back to HTTPS fetch"
fi
REPO_URL="$FETCH_URL"

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "plist: cloning $REPO_URL ($BRANCH) into $REPO_DIR"
  attempt=0
  while [ "$attempt" -lt 30 ]; do
    attempt=$((attempt + 1))
    rm -rf "$REPO_DIR"
    if git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"; then
      break
    fi
    echo "plist: clone failed (attempt $attempt/30) — check repo access for $REPO_URL, retrying in 10s"
    sleep 10
    if [ "$attempt" -ge 30 ]; then
      echo "plist: FATAL could not clone $REPO_URL"
      exit 1
    fi
  done
else
  git -C "$REPO_DIR" remote set-url origin "$REPO_URL"
  dirty=$(git -C "$REPO_DIR" status --porcelain | wc -l | tr -d ' ')
  unpushed=$(git -C "$REPO_DIR" log --oneline "@{u}..HEAD" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$dirty" = "0" ] && [ "$unpushed" = "0" ]; then
    echo "plist: fetching $BRANCH"
    if git -C "$REPO_DIR" fetch --quiet origin "$BRANCH" \
       && git -C "$REPO_DIR" merge --ff-only "origin/$BRANCH"; then
      echo "plist: checkout up to date"
    else
      echo "plist: WARNING fast-forward failed — serving the local checkout as-is"
    fi
  else
    echo "plist: WARNING local changes (dirty=$dirty) or unpushed commits ($unpushed) — NOT pulling, serving local state"
  fi
fi

# Fetch over HTTPS (works on a public repo with no credential); push over SSH
# with the deploy key this container generated. `git push` uses the pushurl.
git -C "$REPO_DIR" remote set-url origin "$REPO_URL"
git -C "$REPO_DIR" remote set-url --delete --push origin >/dev/null 2>&1 || true
git -C "$REPO_DIR" remote set-url --add --push origin "$PUSH_URL"

# Records must be writable by the unprivileged runtime user.
if [ "$(id -u)" = "0" ]; then
  chown -R "$RUN_USER":"$RUN_USER" "$DATA_DIR"
fi

export OPS_REPO_ROOT="$REPO_DIR"
echo "plist: code from /app, records from $REPO_DIR at $(git -C "$REPO_DIR" rev-parse --short HEAD) on $(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)"

if [ "$(id -u)" = "0" ] && command -v gosu >/dev/null 2>&1; then
  exec gosu "$RUN_USER" "$@"
fi
exec "$@"
