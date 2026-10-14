# plist operations control

A small internal operations control system built to answer one question: **What should we work on next?**

The Flask application provides live operational state for Cary and Dax-A as a thin interface over direct Git-backed JSON files. Every mutation directly modifies canonical JSON records using atomic writes (`tempfile` -> `os.replace`), writes an append-only entry to `ops-data/activity/`, produces a daily snapshot, and creates a clear Git commit.

There is no SQLite or relational database. The filesystem and Git repository are the single source of truth for Cary, Dax-A, and Charlie.

---

## Architecture

```text
Cary / Dax-A -> Web UI or JSON API -> Canonical JSON Files
                                            |
                                            +-> Lifecycle files (live/, active/, etc.)
                                            +-> ops-data/tasks/, ops-data/decisions/
                                            +-> ops-data/activity/YYYY-MM.ndjson
                                            +-> ops-data/snapshots/YYYY-MM-DD.json
                                            +-> Git commit

Charlie / Dax-A ---------------------------> reads/writes same Git repository files
```

- **Source of truth:** Direct JSON files in lifecycle folders (`live/`, `active/`, `planning/`, `planned/`, `concept/`) and `ops-data/`.
- **IC Number Allocation:** Managed via root-level `next_ic.json`, owned by Dax-A.
- **Priority Overlay:** Independent priority overlay in `priority/*.json` is applied on top of project records.
- **Audit:** Every mutation records actor, action, object, timestamp, before state, and after state in append-only `ops-data/activity/YYYY-MM.ndjson`.
- **Optimistic Concurrency:** Every mutable record carries an integer `version`. Updates require the version read by the caller; stale updates return HTTP `409 Conflict`.
- **Shared NAS Safety:** Atomic temporary-file replacement is performed within the same directory on `/mnt/dax/code/plist` to eliminate locking hazards across CIFS/SMB mounts.

---

## Minimal Project Fields

Each project is stored as an individual JSON record with the following minimal fields:

- `id`: Monotonic canonical identifier (e.g., `IC-000004`, `PROJECT-0002`)
- `name`: Human-readable initiative title
- `status`: Operational lifecycle status (`live`, `active`, `planning`, `planned`, `concept`)
- `priority`: Manual priority rating (1 to 5)
- `owner`: Responsible actor (`Cary`, `Dax-A`, or unassigned)
- `summary`: Short contextual summary of the initiative
- `next_action`: Immediate next actionable step
- `blocker`: Blocker explanation (if blocked)
- `repo`: Repository link or URL
- `last_updated`: Date of last operational update (`YYYY-MM-DD`)
- `version`: Monotonic integer for optimistic concurrency control

---

## Local Setup

Python 3.11 or later is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
export OPS_GIT_ENABLED=false
flask --app wsgi run --debug
```

Open <http://127.0.0.1:5000>.

### Production / NAS Deployment

```bash
export OPS_GIT_ENABLED=true
export OPS_GIT_REMOTE_PUSH=false
flask --app wsgi run --host 0.0.0.0
```

Useful maintenance commands:

```bash
# Validate integrity of all JSON project records
flask --app wsgi validate

# Generate point-in-time snapshot
flask --app wsgi snapshot

# Sync snapshot and create git commit
flask --app wsgi sync -m "ops: Cary refreshed operational state"

# Run test suite
python -m pytest -q
```

---

## Operational Control & Rules

### Active-Project Policy Violations (At Risk)
The system automatically identifies conditions requiring attention:
- Active project with no owner
- Active project with no next action
- Active project without completion definition or summary
- No activity for 14 days
- Overdue tasks
- Tasks blocked longer than 7 days

### Priority Recommendation (`/api/next`)
`/api/next` orders executable work for Dax-A by Cary's manual priority first. An algorithmic score (`impact * 3 + urgency * 3 - effort`) breaks ties only; it never overrides manual priority. Blocked, completed, cancelled, and parked work is excluded.

---

## API Summary

Send the logical actor in `X-Actor: Cary` or `X-Actor: Dax-A`. Charlie is deliberately read-only.

```bash
# Highest-priority executable work for Dax-A
curl 'http://127.0.0.1:5000/api/next?owner=Dax-A'

# Create a task
curl -X POST http://127.0.0.1:5000/api/tasks \
  -H 'Content-Type: application/json' -H 'X-Actor: Dax-A' \
  -d '{"project_id":"PROJECT-0002","title":"Run validation","owner":"Dax-A","priority":1}'

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

### Main Routes:
- `GET /api/dashboard`, `GET /api/next`
- `GET|POST /api/workstreams`
- `GET|POST /api/projects`, `GET|PATCH /api/projects/:id`, `POST /api/projects/:id/park`
- `GET|POST /api/deliverables`
- `GET|POST /api/tasks`, `GET|PATCH /api/tasks/:id`
- `POST /api/tasks/:id/claim|block|complete`
- `GET /api/decisions/pending`, `POST /api/decisions`, `POST /api/decisions/:id/decide`
- `POST /api/admin/sync` (Cary only)
