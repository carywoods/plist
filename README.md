# plist operations control

A small internal operations control system built to answer one question: **What should we work on next?**

The Flask application provides live operational state for Cary and Dax-A. Every meaningful mutation also produces a deterministic, human-readable mirror under `ops-data/` so Charlie and other repository-only readers can understand current state.

The original lifecycle registry (`active/`, `live/`, `planning/`, `planned/`, `concept/`, and `priority/`) is preserved unchanged. Those files are valuable legacy source records, not a second writable application database.

## Architecture

```text
Cary / Dax-A -> UI or JSON API -> SQLite live database
                                      |
                                      +-> JSON record
                                      +-> monthly NDJSON activity
                                      +-> daily JSON snapshot
                                      +-> Git commit

Charlie --------------------------------> reads Git mirror
```

- **Live source of truth:** SQLite, configured by `OPS_DATABASE`. It is never committed.
- **Repository mirror:** individual JSON records, append-only history represented as deterministic monthly NDJSON, and complete daily JSON snapshots in `ops-data/`.
- **Synchronization:** application to database to Git mirror only. There is no automatic repository-to-database import.
- **Audit:** every mutation records actor, action, object, timestamp, before state, and after state in the database and activity mirror.
- **Concurrency:** every mutable object has a version. Updates and actions require the version read by the caller; stale updates return HTTP `409`.
- **IDs:** stable monotonic IDs such as `PROJECT-0001`, `TASK-0001`, and `DECISION-0001`.

The schema is in `opsapp/schema.sql`. Startup applies its idempotent `CREATE TABLE IF NOT EXISTS` migration and seeds demonstration data only when the project table is empty.

## Local setup

Python 3.11 or later is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
export OPS_GIT_ENABLED=false
flask --app wsgi run --debug
```

Open <http://127.0.0.1:5000>. The zero-configuration development database is created at `/tmp/plist-ops-<uid>/ops.db` because this repository may be on an SMB/CIFS NAS where SQLite locking is unsafe. For any durable deployment, set `OPS_DATABASE` to a persistent **local-disk** path:

```bash
export OPS_DATABASE=/var/lib/plist/ops.db
export OPS_MIRROR_ROOT=/mnt/dax/code/plist/ops-data
export OPS_GIT_ENABLED=true
export OPS_AUTO_SEED=false
export OPS_AUTO_IMPORT_LEGACY=true
flask --app wsgi run --host 0.0.0.0
```

`OPS_GIT_REMOTE_PUSH` defaults to `false`. A successful mutation commits locally; enable pushing only after credentials and the operational policy are established. Demo seeding defaults on for a zero-configuration prototype and synchronizes the initial mirror; set `OPS_AUTO_SEED=false` for a real deployment. Copy `.env.example` into your service manager’s secret/environment configuration—do not commit a populated `.env`.

Useful maintenance commands:

```bash
flask --app wsgi init-db
flask --app wsgi sync-mirror
flask --app wsgi snapshot
flask --app wsgi import-legacy --dry-run
python -m pytest -q
```

## Data model

The hierarchy is `Workstream -> Project -> Deliverable -> Task`, plus lightweight decisions.

- A workstream is a continuing organizational area.
- A project is a defined initiative with an owner, manual priority, and completion definition.
- A deliverable is a concrete outcome with a definition of done.
- A task is executable work. Tasks carry manual priority plus optional impact, urgency, and effort inputs.
- A decision contains context and a recommendation for Cary’s judgment.

Active-project policy violations are visible under **At Risk**: missing owner, missing executable next action, missing completion definition, no activity for 14 days, overdue work, or a task blocked longer than seven days.

`/api/next` orders executable work by Cary’s manual priority first. A transparent suggestion score (`impact * 3 + urgency * 3 - effort`) breaks ties only; it never overrides manual priority. Blocked, completed, cancelled, parked, and non-ready work is excluded.

## API

Send the logical actor in `X-Actor: Cary` or `X-Actor: Dax-A`. Charlie is deliberately read-only. This actor header is attribution and prototype authorization, not production authentication.

```bash
# Highest-priority executable work for Dax-A
curl 'http://127.0.0.1:5000/api/next?owner=Dax-A'

# Create a task
curl -X POST http://127.0.0.1:5000/api/tasks \
  -H 'Content-Type: application/json' -H 'X-Actor: Dax-A' \
  -d '{"project_id":"PROJECT-0001","title":"Run validation","owner":"Dax-A","priority":1}'

# Claim the version just read
curl -X POST http://127.0.0.1:5000/api/tasks/TASK-0001/claim \
  -H 'Content-Type: application/json' -H 'X-Actor: Dax-A' \
  -d '{"version":1}'

# Record a blocker
curl -X POST http://127.0.0.1:5000/api/tasks/TASK-0001/block \
  -H 'Content-Type: application/json' -H 'X-Actor: Dax-A' \
  -d '{"version":2,"blocker_reason":"Waiting for Cary approval"}'

# Complete with notes
curl -X POST http://127.0.0.1:5000/api/tasks/TASK-0001/complete \
  -H 'Content-Type: application/json' -H 'X-Actor: Dax-A' \
  -d '{"version":3,"completion_notes":"Validation accepted"}'
```

Main routes:

- `GET /api/dashboard`, `/api/next`
- `GET|POST /api/workstreams`
- `GET|POST /api/projects`, `GET|PATCH /api/projects/:id`, `POST /api/projects/:id/park`
- `GET|POST /api/deliverables`
- `GET|POST /api/tasks`, `GET|PATCH /api/tasks/:id`
- `POST /api/tasks/:id/claim|block|complete`
- `GET /api/decisions/pending`, `POST /api/decisions`, `POST /api/decisions/:id/decide`
- `POST /api/admin/sync` (Cary only) to repair/rebuild the complete mirror

Cary alone may create/change/park projects and decide requests. Dax-A may create and execute tasks, create deliverables, and request decisions. Deletion and strategic cancellation endpoints are intentionally absent.

## Mirror and Git behavior

Each successful mutation writes, using temporary-file plus atomic replacement where supported:

```text
ops-data/workstreams/WORKSTREAM-0001.json
ops-data/projects/PROJECT-0001.json
ops-data/deliverables/DELIVERABLE-0001.json
ops-data/tasks/TASK-0001.json
ops-data/decisions/DECISION-0001.json
ops-data/activity/2026-10.ndjson
ops-data/snapshots/2026-10-12.json
```

Only affected mirror paths are staged. Commit messages are readable, for example `ops: Dax-A completed TASK-0001`. Unrelated working-tree changes are not staged.

The database commit occurs before mirror/Git synchronization. If file generation or Git fails, the API returns `503` with `database_committed: true` and `mirrored: false`, and records an unresolved sync failure on the Activity page. It never claims success. After correcting disk, permissions, or Git configuration, Cary can run `flask --app wsgi sync-mirror` or `POST /api/admin/sync` to rebuild the complete mirror and mark failures resolved.

Daily snapshots include every current record and the complete audit sequence, sufficient for later recovery tooling. Automated restore is intentionally not implemented because import must be an explicit administrative action rather than uncontrolled bidirectional sync.

## Existing-data migration

On startup, `OPS_AUTO_IMPORT_LEGACY=true` idempotently reads canonical numbered JSON records from `live/`, `active/`, `planning/`, `planned/`, and `concept/`. It currently finds 94 projects, links the seeded CommPilot instead of duplicating it, and ignores unnumbered alias/priority-overlay files. Original files are never rewritten.

Lifecycle maps to operational status as follows: `live` and `active` become `active`; `planning` and `planned` become `incubating`; `concept` becomes `parked`. Unknown owners and completion definitions remain empty rather than being invented, so active records correctly appear under **At Risk** until Cary triages them. `flask --app wsgi import-legacy --dry-run` previews the repeatable operation; `flask --app wsgi import-legacy` applies any records not already imported.

## Deployment considerations

- Put SQLite on persistent local storage, not the shared NAS. Back it up normally; the Git mirror is the independent readable recovery copy.
- Run one application process for this SQLite prototype. SQLite serializes writes, while record versions reject stale logical updates. Move to PostgreSQL if write concurrency or high availability grows.
- Keep the repository checkout and `ops-data/` writable by the service account. Do not use NAS file ownership as authorization.
- Run the Flask application behind a production WSGI server and TLS reverse proxy.
- Add real identity authentication before exposing the service beyond a trusted private network. The prototype trusts `X-Actor`.
- Configure Git author identity, credentials, monitoring, and remote-push policy explicitly.
- Schedule `flask --app wsgi snapshot` daily even on days with no mutations.
- Keep secrets in environment/service-manager configuration, never in the repository.

## Current limitations

- Logical actors are implemented, but authentication, sessions, CSRF protection, and transport security are deployment work.
- SQLite is suitable for a single small service, not multiple application hosts.
- Git commit is synchronous for reliability and visibility; a slow NAS or Git remote increases mutation latency.
- Remote push is optional and off by default. Local Git commits still provide audit/recovery history.
- Recovery from a snapshot and legacy import are explicit future administrative operations.
- The UI covers the operational dashboard and common execution/decision actions; broader record editing is currently API-first.

The prototype deliberately omits chat, CRM, Gantt charts, timesheets, document editing, custom workflows, and generic idea-management features.
