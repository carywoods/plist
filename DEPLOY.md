# Deploying plist (Coolify container, tailnet access)

The board runs as one container on the Coolify cluster. It is a **single-writer**
service: the container owns the writable checkout, and every mutation is
committed and pushed to `carywoods/plist` inside the same request.

## Two stores: code in the image, records on the volume

A plist record *is* a file in this repository (`live/`, `active/`, `ops-data/`, ...).
There is no database to put elsewhere, so:

- **Code is baked into the image** at `/app` (`wsgi.py`, `opsapp/`). One image is
  one version of the code, and a code change ships by rebuilding it.
- **Records live in a git checkout on the `/data` volume** at `/data/plist`.
  `OPS_REPO_ROOT` points the app at that checkout, so record reads and writes
  never touch the image.
- Consequences: a **code** change needs a **Redeploy** (rebuild); a **record**
  change needs a **Restart** (the entrypoint fast-forwards the checkout from
  `origin/main` on every start, and every in-app mutation is committed and
  pushed inside the same request).
- `origin` is split on purpose: **fetch over HTTPS** (works on the public repo
  with no credential) and **push over SSH** with the deploy key
  (`OPS_GIT_REMOTE_URL` / `OPS_GIT_PUSH_URL` override both).
- Never start a second container against the same volume: writes would interleave.
- The volume also holds `/data/ssh/id_ed25519`, a deploy key the container
  generates for itself on first boot. Its public half is printed to the
  container log; it needs **write access** on the GitHub repository for
  `OPS_GIT_REMOTE_PUSH=true` to work.
- `docker-compose.yml` runs the same image locally (`docker compose up -d --build`).

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
| `OPS_REPO_ROOT` | record root. Set to `/data/plist` in the image; only override when running the code outside a container. |
| `OPS_RUN_USER` | `plist` — the unprivileged user the entrypoint drops to before starting gunicorn |

`OPS_GIT_USER_NAME` / `OPS_GIT_USER_EMAIL` default to `Dax-A <herm@indybiosystems.com>`
and are what show up as the committer.

## Container shape

- Build: multi-stage `Dockerfile` (Coolify build pack = Dockerfile). Stage 1
  installs dependencies into a venv from `pyproject.toml`; stage 2 adds git,
  `gosu`, a `plist` user, and the code from the build context.
- The entrypoint starts as root **only** to own the volume and prepare the git
  checkout, then `exec gosu plist …` — gunicorn never runs as root.
- Exposed port: `8000`. Health check path: `/health` (public, no password), wired
  as a Docker `HEALTHCHECK`.
- Volume: `/data` (holds the checkout + the deploy key). Record directories are
  excluded from the build context by `.dockerignore` — the image never ships a
  stale copy of the board.
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

## How the deployed copy stays in synch

GitHub `main` is the source of truth. Sync runs in one direction continuously
and the other only at boot:

| Direction | When | Mechanism |
| --- | --- | --- |
| app → GitHub | every mutation | `git_commit()` + push with the container's deploy key, inside the request |
| GitHub → app | container **start only** | entrypoint fetch + `merge --ff-only`, only when the checkout is clean and has nothing unpushed |

There is no polling loop, no cron, and no background sync worker in the app —
nothing pulls between restarts. The container serves whatever tree it booted
with.

### The NAS checkout is a read-only mirror

The app is the **only writer**. `/mnt/dax/code/plist` exists to be read:

- Never edit, commit, or push from it. Refresh it with
  `sh scripts/nas_mirror.sh` (fetch + `merge --ff-only`; refuses on a dirty
  tree). The mount is `file_mode=0664`, so the script has no executable bit —
  run it through `sh`, not `./scripts/nas_mirror.sh`.
- That copy carries a local write guard: `pre-commit` and `pre-push` hooks,
  installed via `core.hooksPath` rather than `.git/hooks`, because the CIFS
  mount is `file_mode=0664` and cannot store an executable bit — hooks inside
  `.git/hooks` are silently ignored there. The guard refuses unless
  `PLIST_WRITE_OK=1` is set for that single command.
- A peer write is invisible to the running app and is how the deployed copy
  goes stale. Worse, if the container also holds local commits the boot-time
  merge refuses and it silently serves local state.

### Getting an out-of-band commit live

Only for the approved exception (a fix pushed straight to `main`, e.g. while the
app is down):

1. Push to `main`.
2. Restart the app (Coolify → application → Restart). No rebuild is needed — the
   entrypoint fast-forwards the volume on start.
3. Verify before reporting it done:
   ```bash
   ssh root@<node> 'docker exec <container> git -C /data/plist rev-parse --short HEAD'
   git ls-remote git@github.com:carywoods/plist.git refs/heads/main
   ```
   The two hashes must match, and the record must be visible via
   `/api/projects`.

### If the container refuses to fast-forward

The entrypoint logs `WARNING local changes (dirty=N) or unpushed commits (N) —
NOT pulling, serving local state`. That means something else wrote to the
volume. Reconcile the volume's commit back into `main`, then restart. Never
`git reset` the volume while the app is serving.
