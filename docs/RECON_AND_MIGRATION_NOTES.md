# plist Architecture Transition & Reconnaissance Notes

**Date:** 2026-10-14  
**Author / Actor:** Emi (AI Technical Lead)  
**Stakeholder / Authority:** Dr. Cary Woods (Director & Principal)  
**System:** `carywoods/plist` Operational Control System  
**Commit Reference:** `67cf016` (Refactor), `7afba64` (Pre-refactor Checkpoint)

---

## 1. Executive Summary

This document captures the reconnaissance evaluation, failure analysis, and complete architectural migration executed on 2026-10-14 to salvage the Codex-built web UI of `plist` while permanently removing SQLite in favor of **direct Git-backed JSON persistence**.

The prior architecture attempted to run SQLite over a CIFS/SMB-mounted NAS share (`/mnt/dax/code/plist`), which failed due to unreliable POSIX advisory locking and shared-memory semantics on CIFS. 

The replacement storage engine ([`opsapp/storage.py`](file:///mnt/dax/code/plist/opsapp/storage.py)) establishes the filesystem itself as the single source of truth. The Flask web UI and REST API now operate as thin layers directly over the canonical project records across the lifecycle folders (`live/`, `active/`, `planning/`, `planned/`, `concept/`) and `ops-data/`.

---

## 2. Reconnaissance Report (Pre-Migration Findings)

### 2.1 Current Application Architecture (As Found)
* **Stack:** Python 3.11+ / Flask 3.0 / Jinja2 server-rendered templates / CSS.
* **Original Flow:** `UI/API -> SQLite Live DB -> ops-data/ Git Mirror Files -> Git Commit`.
* **Issue:** Dual-state architecture with an uncommitted shadow SQLite database in `/tmp/plist-ops-1000/ops.db` and a 0-byte uninitialized stub at `instance/ops.db`.

### 2.2 Where SQLite Was Used
* `opsapp/db.py`: Database connection, PRAGMAs (`busy_timeout`, `journal_mode = DELETE`), ID counters table, activity insertion.
* `opsapp/schema.sql`: 8 tables (`counters`, `workstreams`, `projects`, `deliverables`, `tasks`, `decisions`, `activity`, `sync_failures`) and DDL indexes.
* `opsapp/service.py`: Direct SQL queries embedded throughout all business logic (`SELECT`, `INSERT`, `UPDATE ... WHERE id = ? AND version = ?`).
* `opsapp/mirror.py`: SQL queries to mirror rows back into JSON and NDJSON.
* `opsapp/legacy.py`: Batch SQL inserts on startup for 94 lifecycle projects.
* `opsapp/routes.py`: SQL queries for activity logs and sync failure resolution.

### 2.3 Files Implementing Persistence
* `opsapp/db.py`, `opsapp/schema.sql`, `opsapp/service.py`, `opsapp/mirror.py`, `opsapp/legacy.py`.

### 2.4 Independent UI Components (Retained)
* `opsapp/templates/base.html`: 100% independent.
* `opsapp/templates/dashboard.html`: 100% independent; consumes structured dictionary.
* `opsapp/templates/projects.html`: Card grid and creation forms.
* `opsapp/templates/project.html`: Project detail facts, tasks, deliverables, and update forms.
* `opsapp/templates/tasks.html` & `decisions.html`: Form-driven execution and judgment queues.
* `opsapp/templates/activity.html`: Append-only audit log timeline.
* `opsapp/static/app.css` & `opsapp/static/actions.css`: Responsive styles.

### 2.5 Feasibility of Direct JSON Conversion
* **Result:** High feasibility. The application was already serializing state to clean JSON via atomic write primitives. Cutting out SQLite reduced code volume, eliminated synchronization lag, and restored architectural honesty.

### 2.6 Architectural Risks on CIFS Share & Mitigations
* **Risk 1: Incomplete Writes / Corruption:**
  * *Mitigation:* Temporary file written *in the same directory* (`dir=path.parent`), followed by `os.fsync()` and atomic `os.replace()`.
* **Risk 2: Multi-Actor Concurrency Conflicts:**
  * *Mitigation:* Explicit monotonic `version` field in every mutable JSON record. Outdated version updates return `HTTP 409 Conflict`.
* **Risk 3: Attribute Caching Latency:**
  * *Mitigation:* Reads directly read file contents rather than cached stat metadata.
* **Risk 4: Git Conflict / Dirty Worktree:**
  * *Mitigation:* No automatic branch switching, no force pushing, explicit file staging only.

---

## 3. Implemented Architecture: Direct JSON Persistence

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

### 3.1 Single Source of Truth
* **Lifecycle Directories:**
  * `live/`: Live in production / active operational tools.
  * `active/`: Active initiatives under active development.
  * `planning/`: Actively planned / near-term execution.
  * `planned/`: Backlog initiatives with defined outcomes.
  * `concept/`: Incubating and parked ideas.
* **Priority Overlays:** `priority/*.json` dynamically overlays priorities onto matching project records.
* **Tasks & Decisions:** Stored in `ops-data/tasks/TASK-*.json` and `ops-data/decisions/DECISION-*.json`.
* **Append-Only Activity:** Stored in `ops-data/activity/YYYY-MM.ndjson`.
* **Daily Snapshots:** Point-in-time state dumped to `ops-data/snapshots/YYYY-MM-DD.json`.

### 3.2 IC Number Allocation (`next_ic.json`)
* Root-level file [`next_ic.json`](file:///mnt/dax/code/plist/next_ic.json) tracks monotonic IC allocation:
  ```json
  {
    "next_ic_number": 94,
    "next_id": "IC-000094",
    "updated_at": "2026-10-14T09:30:00Z",
    "owner": "Dax-A",
    "notes": "Tracks next available IC number for Dax-A to assign to new projects."
  }
  ```
* Dax-A owns opening new projects and assigning sequential IC numbers.

### 3.3 Minimal Project Schema
Every canonical project file supports:
```json
{
  "id": "IC-000004",
  "name": "CommPilot (email AI assistant)",
  "status": "live",
  "priority": 1,
  "owner": "Cary",
  "summary": "Email assistant production validation",
  "next_action": "Run production validation suite",
  "blocker": null,
  "repo": "https://github.com/carywoods/commpilot",
  "last_updated": "2026-10-14",
  "version": 1
}
```

---

## 4. File-by-File Audit & Status

| File | Status | Notes |
| :--- | :--- | :--- |
| `instance/ops.db` | **Deleted** | 0-byte CIFS failure stub removed. |
| `opsapp/db.py` | **Deleted** | SQLite connection pooling & DDL removed. |
| `opsapp/schema.sql` | **Deleted** | Relational SQL schema removed. |
| `opsapp/legacy.py` | **Deleted** | Startup SQL ingestion bridge removed. |
| `opsapp/mirror.py` | **Deleted** | Redundant SQLite-to-mirror synchronization removed. |
| `opsapp/storage.py` | **Created** | Direct JSON engine (atomic writes, concurrency, indexing). |
| `opsapp/service.py` | **Refactored** | Business logic refactored to consume `storage.py`. |
| `opsapp/routes.py` | **Refactored** | UI/API routes routed through `service.py` and `storage.py`. |
| `opsapp/cli.py` | **Refactored** | Replaced `init-db` with `validate`, `snapshot`, `sync`. |
| `opsapp/__init__.py` | **Refactored** | App factory cleaned of all database configuration. |
| `opsapp/templates/project.html` | **Adapted** | Direct display & form editing of minimal fields. |
| `opsapp/templates/projects.html` | **Adapted** | Shows portfolio count (99) and lifecycle statuses. |
| `tests/conftest.py` | **Adapted** | Temporary Git-backed JSON test fixtures. |
| `tests/test_ops.py` | **Adapted** | Comprehensive unit & integration tests (8 passed). |
| `README.md` | **Updated** | Comprehensive operational manual & architecture documentation. |
| `next_ic.json` | **Created** | Root-level counter for Dax-A IC allocation. |
| `active/project-0002-operations-control-prototype.json` | **Created** | Canonical home for `PROJECT-0002` in `active/`. |

---

## 5. Verification & Telemetry

1. **Integrity Validation:**
   ```bash
   $ flask --app wsgi validate
   Loaded 99 projects across lifecycle folders.
   Loaded 5 tasks.
   Loaded 2 decisions.
   All records validated successfully. Integrity OK.
   ```
2. **Automated Test Suite:**
   ```bash
   $ pytest -q
   8 passed in 0.77s
   ```
3. **Live Operational Telemetry:**
   * `/api/next?owner=Dax-A`: Successfully returns `PROJECT-0002` ("Migrate from SQLite to direct Git-backed JSON persistence") as top priority executable task.
   * `/api/dashboard`: Correctly evaluates 112 rule violations across untriaged legacy records (missing owners, next actions, completion definitions) under **At Risk**.
   * Web Pages (`/`, `/projects`, `/projects/IC-000004`, `/projects/PROJECT-0002`, `/tasks`, `/decisions`, `/activity`): All render HTTP 200.

---

## 6. Git History & Safety Checkpoints

* **Checkpoint Commit:** `7afba64` (`checkpoint: save pre-refactor state with SQLite opsapp and ops-data`)
* **Migration Commit:** `67cf016` (`refactor: replace SQLite persistence with direct Git-backed JSON persistence`)
* **Local DB Backup:** `/tmp/plist-ops-1000-backup-20261014.db` preserved on host.
