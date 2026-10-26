# Deploying plist (Coolify container, tailnet access)

The board runs as one container on the Coolify cluster. It is a **single-writer**
service: the container owns the writable checkout, and every mutation is
committed and pushed to `carywoods/plist` inside the same request.

## Why the code lives on a volume

A plist record *is* a file in this repository (`live/`, `active/`, `ops-data/`, ...).
There is no database to put elsewhere, so the container keeps a git checkout at
`/data/plist` on a persistent volume and runs from it. Consequences:

- The image is runtime only (Python, gunicorn, git). Redeploying the image does
  not change the served code by itself — the entrypoint fast-forwards the
  checkout from `origin/main` on every start, which is what makes a push from
  Cary's desktop (or from Dax-A) live on the next restart.
- Never start a second container against the same volume: writes would interleave.
- The volume also holds `/data/ssh/id_ed25519`, a deploy key the container
  generates for itself on first boot. Its public half is printed to the
  container log; it needs **write access** on the GitHub repository for
  `OPS_GIT_REMOTE_PUSH=true` to work.

## Environment

| Variable | Value in deployment |
| --- | --- |
| `OPS_PASSWORD` | shared password (Cary + Dax-A). Required outside `TESTING` — the app raises instead of serving unprotected. |
| `OPS_SECRET_KEY` | random 32+ bytes (`openssl rand -hex 32`). The placeholder is rejected. |
| `OPS_GIT_ENABLED` | `true` |
| `OPS_GIT_REMOTE_PUSH` | `true` — a written-but-unpushed record is a silent break, not a cosmetic issue |
| `OPS_SESSION_DAYS` | `30` (login cookie lifetime) |
| `OPS_PUBLIC_HOST` | optional extra hostname accepted by the same-origin check when behind a proxy |
| `OPS_COOKIE_SECURE` | `true` only when served over HTTPS |

`OPS_GIT_USER_NAME` / `OPS_GIT_USER_EMAIL` default to `Dax-A <herm@indybiosystems.com>`
and are what show up as the committer.

## Container shape

- Build: `Dockerfile` (Coolify build pack = Dockerfile).
- Exposed port: `8000`. Health check path: `/health` (public, no password).
- Volume: `/data` (holds the checkout + deploy key).
- gunicorn runs **one worker and one thread**. `next_ic.json` allocation has no
  cross-process lock, so raising concurrency can hand two projects the same IC
  number.

## Access

Credentials accepted: session cookie from `/login`, `X-Ops-Key: <password>`
header, or HTTP Basic. `X-Actor` remains attribution-only.

For tailnet-only access, publish the container port on the node and reach it at
the node's tailnet address (or a MagicDNS name); do not attach a public domain
unless Cary wants the board on the open internet behind Cloudflare.

## Verification

```bash
curl -s http://<node>:<port>/health                     # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' http://<node>:<port>/   # 302 -> /login
curl -s -H "X-Ops-Key: $OPS_PASSWORD" http://<node>:<port>/api/projects | head -c 200
```

A write is verified end to end only when the GitHub `main` HEAD has moved:
`git ls-remote git@github.com:carywoods/plist.git refs/heads/main` should equal
the commit the API response reports in `sync.git_commit`.
