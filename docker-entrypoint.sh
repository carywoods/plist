#!/bin/sh
# plist container entrypoint.
#
# The volume at $DATA_DIR holds two things:
#   $DATA_DIR/plist  — the git checkout (code + every record; the source of truth)
#   $DATA_DIR/ssh    — the deploy key this container generated for itself
#
# The deploy key is created on first boot and printed once, so the volume owns
# its own credential: nothing secret is baked into the image or passed in argv.
set -eu

REPO_URL="${OPS_GIT_REMOTE_URL:-https://github.com/carywoods/plist.git}"
PUSH_URL="${OPS_GIT_PUSH_URL:-git@github.com:carywoods/plist.git}"
BRANCH="${OPS_GIT_BRANCH:-main}"
DATA_DIR="${OPS_DATA_VOLUME:-/data}"
REPO_DIR="${PLIST_REPO_DIR:-$DATA_DIR/plist}"
SSH_DIR="$DATA_DIR/ssh"
KEY="$SSH_DIR/id_ed25519"

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

git config --global user.name "${OPS_GIT_USER_NAME:-Dax-A}"
git config --global user.email "${OPS_GIT_USER_EMAIL:-herm@indybiosystems.com}"
git config --global --add safe.directory "$REPO_DIR"

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

cd "$REPO_DIR"
# Fetch over HTTPS (works on a public repo with no credential); push over SSH
# with the deploy key this container generated. `git push` uses the pushurl.
git remote set-url origin "$REPO_URL"
git remote set-url --delete --push origin >/dev/null 2>&1 || true
git remote set-url --add --push origin "$PUSH_URL"
echo "plist: running $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD) from $REPO_DIR"
exec "$@"
