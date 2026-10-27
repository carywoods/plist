#!/bin/sh
# Refresh the NAS copy of plist from GitHub — READ ONLY.
#
# The deployed app (Coolify) is the only writer of this repository. This
# checkout exists to be read, so this script only ever fast-forwards it.
# It refuses to touch a dirty tree rather than discarding anything.
#
#   scripts/nas_mirror.sh          # fetch + merge --ff-only, report HEAD
#   scripts/nas_mirror.sh --check  # report only, no network, no changes
set -eu

BRANCH="${OPS_GIT_BRANCH:-main}"
BRANCH_REMOTE="${PLIST_FETCH_URL:-git@github.com:carywoods/plist.git}"
HERE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

cd "$HERE"

head=$(git rev-parse --short HEAD)

if [ "${1:-}" = "--check" ]; then
  echo "plist mirror: HEAD $head on $(git rev-parse --abbrev-ref HEAD)"
  if [ -n "$(git status --porcelain)" ]; then
    echo "plist mirror: DIRTY (reads are fine, but this copy is no longer a pure mirror)"
    git status --short | head -10
  else
    echo "plist mirror: clean"
  fi
  exit 0
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "plist mirror: REFUSING to pull — working tree is dirty." >&2
  echo "  This copy is a read-only mirror. Move any real change through the app." >&2
  git status --short | head -10 >&2
  exit 1
fi

echo "plist mirror: fetching $BRANCH from $BRANCH_REMOTE"
git fetch --quiet "$BRANCH_REMOTE" "$BRANCH"
git merge --ff-only FETCH_HEAD
echo "plist mirror: $head -> $(git rev-parse --short HEAD)"
