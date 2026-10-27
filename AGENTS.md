# AGENTS.md

## Purpose

This repository supports an internal operational control system whose primary purpose is to answer:

**What should we work on next?**

The system is not intended to become a generic project-management platform, CRM, idea registry, or collaboration suite. Avoid Jira/Asana/Monday feature bloat (no Gantt charts, timesheets, chat, social feeds, or workflow engines).

Keep the application focused on operational state, priorities, ownership, next actions, decisions, and organizational memory.

**Headline directive:** Stop redesigning `plist`; use it. Data curation is progressive.

---

## Architectural Evolution & Failure History (Do Not Repeat)

The architecture of `plist` was radically simplified on 2026-10-14 to eliminate a fundamentally flawed dual-state design. Every agent working in this repository must understand this history so the rejected pattern is never re-introduced.

### Checkpoint Commits for Reference:
- `7afba64`: Checkpoint saving pre-refactor state with SQLite database and separate `ops-data/` Git mirror.
- `67cf016`: Complete refactor replacing SQLite persistence with direct Git-backed JSON persistence.
- `26d54b6`: Baseline ratification and verification notes from Charlie and Dax-A.

### Why the Dual-State SQLite Architecture Failed:
1. **Network Filesystem Locking Hazards:** The live repository is hosted on a CIFS/SMB-mounted NAS share (`/mnt/dax/code/plist`). SQLite running over CIFS suffered from unreliable POSIX advisory file locking, broken shared-memory semantics (`wal` mode failure), and locking timeouts.
2. **Dual Source of Truth / Desynchronization:** The previous flow (`API/UI -> SQLite live DB -> ops-data/ Git Mirror -> Git Commit`) created two disconnected operational worlds. The SQLite database frequently failed to commit to the share, falling back to an uncommitted shadow database in `/tmp/` and leaving a 0-byte uninitialized stub at `instance/ops.db`.
3. **Silent Secondary Writes & Swallowed Failures:** `ops-data/projects/` was maintained as a best-effort second copy with swallowed failure paths (`try: ... except: pass`), leading to state divergence where records were reachable only through duplicate paths and record counts mismatched canonical lifecycle files.

### The Immutable Architectural Rule:
**There is exactly ONE operational state: the Git repository filesystem.**
There is no SQLite database. There is no second database engine. There is no background synchronization loop between an operational database and a mirror. The Flask application and REST API operate directly as a thin layer over canonical JSON files on disk.

---

## Working Environment & Shared NAS Safety

The repository is hosted on a shared NAS mounted at:

`/mnt/dax/code/plist`

Cary, Dax-A, and other operational agents access this NAS share across different hosts.

### Rules for Safe CIFS/SMB File Operations:
- **No Reliance on Advisory Locking:** Avoid SQLite, file locking libraries (`fcntl`, `flock`), or cross-process mutexes that depend on local POSIX filesystem semantics.
- **Atomic Writes:** Every file modification must use atomic replacement:
  1. Write to a temporary file located *in the exact same directory* as the target file (`dir=path.parent`).
  2. Flush and force write buffers to physical media via `os.fsync(handle.fileno())`.
  3. Atomically replace the target file via `os.replace(temp_path, target_path)`.
- **Identity & Authorization:** Do not assume identical Linux UID/GID values across machines. Do not use `chmod` or filesystem permissions as an application security boundary.
- **Fail Fast & Loudly:** Never swallow filesystem write errors. If a write or Git operation fails, surface the error immediately.

### Write Path: the Deployed App Is the Only Writer

The live application (Coolify) owns the writable checkout on its volume and commits + pushes every mutation. That makes it the system's **single writer**.

- **The NAS checkout is a read-only mirror.** Do not edit, commit, or push from `/mnt/dax/code/plist`. Inspect it freely; refresh it with `scripts/nas_mirror.sh` (fetch + `merge --ff-only`, refuses on a dirty tree). This copy carries a local write guard (see `DEPLOY.md`) that refuses `commit` and `push` unless `PLIST_WRITE_OK=1` is set for that one command.
- **Why:** a second writer cannot be seen by the deployed copy. The container fast-forwards from `origin/main` only at start, so a peer write leaves the live app serving stale state, and if the container holds local commits of its own the boot-time merge refuses and it silently keeps serving local state.
- **Out-of-band commits are an approved exception, not a workflow.** If one is genuinely needed (e.g. a record fix while the app is down), push to `main`, restart the app, and verify the container's `HEAD` equals `main` before reporting it done.

---

## Current Actors & Authority Boundaries

### 1. Cary
Cary is the organizational owner and final authority.
Cary may:
- Set strategy and organizational priorities.
- Create and modify workstreams.
- Create, modify, park, or archive projects.
- Approve spending and decisions.
- Assign work across human and digital staff.
- Manually override any algorithmic priority recommendation (Cary's priority is absolute).

### 2. Dax-A
Dax-A is a trusted operational staff agent.
Dax-A may:
- Read current operational state.
- Open new projects and allocate sequential IC numbers via `next_ic.json`.
- Create deliverables and tasks.
- Claim executable tasks.
- Update task status and progress.
- Mark tasks blocked and record specific blocker explanations.
- Complete tasks and attach completion notes.
- Create follow-up tasks.
- Recommend priorities based on impact, urgency, and effort.
- Request decisions from Cary.
- **Restriction:** Dax-A does not independently make strategic decisions (parking/archiving initiatives, approving spend, or deciding pending decisions) unless explicitly authorized by Cary.

### 3. Charlie
Charlie is ChatGPT (acting via Git PRs and repository reviews).
- Charlie does not have direct access to the live API or local network.
- Charlie interacts with the system through Git branches and pull requests (e.g., `origin/charlie`).
- Charlie is strictly **read-only** from the perspective of live system operations. Operational mutations are never directly attributed to Charlie without review.

---

## Security & Deployment Boundary

- **Attribution vs. Authentication:** In this prototype, the `X-Actor` HTTP header is for audit attribution and logging, not cryptographic authentication. Access control (shared password / token authentication) is pending implementation.
- **Deployment Boundary:** The application must remain strictly confined to the internal local LAN and Tailscale mesh (`tailnet`).
- **Public Exposure Prohibition:** Never expose the HTTP port to public or untrusted networks without an access-control reverse proxy or gateway.

---

## Core Objects & Directory Hierarchy

The single operational state is structured as follows:

```text
/mnt/dax/code/plist/
├── live/                   # Canonical projects running in production
├── active/                 # Canonical projects under active development
├── planning/               # Canonical projects actively planned for near-term
├── planned/                # Canonical backlog initiatives with defined outcomes
├── concept/                # Canonical incubating and parked ideas
├── priority/               # Priority overlay JSON files
├── next_ic.json            # Monotonic IC allocation counter
└── ops-data/
    ├── tasks/              # Executable tasks (TASK-*.json)
    ├── decisions/          # Judgment requests (DECISION-*.json)
    ├── deliverables/       # Concrete outcomes (DELIVERABLE-*.json)
    ├── workstreams/        # Ongoing activity domains (WORKSTREAM-*.json)
    ├── activity/           # Append-only audit logs (YYYY-MM.ndjson)
    └── snapshots/          # Daily point-in-time state dumps (YYYY-MM-DD.json)
```

> [!IMPORTANT]
> The directory `ops-data/projects/` has been permanently retired and deleted. Canonical project records live strictly in the lifecycle folders (`live/`, `active/`, `planning/`, `planned/`, `concept/`).

### Workstream (`ops-data/workstreams/`)
Represents an ongoing domain of organizational activity (e.g., Infrastructure, Publishing, Product Operations).
Fields: `id`, `name`, `description`, `owner`, `created_at`, `created_by`, `updated_at`, `updated_by`, `version`.

### Project (Lifecycle Folders)
Represents a defined initiative.
Canonical fields:
- `id`: Monotonic identifier (`IC-XXXXXX` or `PROJECT-XXXX`)
- `name`: Initiative title
- `status`: Lifecycle status (`live`, `active`, `planning`, `planned`, `concept`)
- `priority`: Manual priority rating (1 to 5; 1 is highest)
- `owner`: Responsible actor (`Cary`, `Dax-A`)
- `summary`: Context and goals
- `next_action`: Immediate concrete next step
- `completion_definition`: Objective definition of done
- `blocker`: Blocker description (or `null`)
- `repo`: Repository link or URL
- `workstream_id`: Associated workstream
- `version`: Monotonic integer for optimistic concurrency
- `created_at`, `created_by`, `updated_at`, `updated_by`, `last_activity`

### Deliverable (`ops-data/deliverables/`)
Represents a concrete milestone or deliverable within a project.
Fields: `id`, `project_id`, `title`, `description`, `owner`, `status`, `due_date`, `definition_of_done`, `version`.

### Task (`ops-data/tasks/`)
Represents an executable unit of work.
Fields: `id`, `project_id`, `deliverable_id`, `title`, `description`, `owner`, `status` (`ready`, `claimed`, `in_progress`, `waiting`, `blocked`, `completed`, `cancelled`), `priority`, `impact`, `urgency`, `effort`, `requires_cary`, `delegatable`, `blocked`, `blocker_reason`, `due_date`, `completion_notes`, `completed_at`, `version`.

### Decision (`ops-data/decisions/`)
Represents a judgment, policy choice, or spending authorization requiring Cary.
Fields: `id`, `project_id`, `title`, `context`, `recommendation`, `requested_by`, `status` (`pending`, `decided`), `decision`, `decided_by`, `decided_at`, `version`.

---

## IC Number Allocation (`next_ic.json`)

Root-level file `next_ic.json` coordinates sequential project identifiers:

```json
{
  "next_ic_number": 95,
  "next_id": "IC-000095",
  "updated_at": "2026-10-14T14:45:00Z",
  "owner": "Dax-A",
  "notes": "Tracks next available IC number for Dax-A or Cary to assign to new projects."
}
```

### Allocation Rules:
1. Both Dax-A and Cary have authority to open projects and allocate IC numbers.
2. Allocating an IC number, writing the new project JSON file, updating `next_ic.json`, and writing the audit entry must occur as a **single logical operation** committed together in Git.

---

## Mandatory Active-Project Rule & Progressive Curation

### The Invariant:
Every active project (`status: "active"` or in `active/`) must have:
- an assigned **owner**,
- an immediate **next action**, and
- a clear **definition of completion**.

The application flags rule violations automatically on the dashboard under **At Risk**.

### Progressive Curation:
- The legacy portfolio contains over 100 historical records with missing owners, unpopulated next actions, or undefined completion criteria.
- **Do not relax validation rules to make the dashboard look clean.**
- The warnings indicate real operational debt. Resolving these violations progressively as initiatives are touched is normal operational work.

---

## Priority and `/api/next`

### Authority Principle:
Algorithmic scoring (`impact * 3 + urgency * 3 - effort`) assists decision-making, but **Cary's manual priority is absolute.**

### `/api/next` Endpoint Behavior:
When Dax-A or another actor asks "What should I work on next?":
1. The endpoint returns the highest manual priority task assigned to the actor (or unassigned delegatable work).
2. Algorithmic scores break ties only among tasks with identical manual priority.
3. Blocked, completed, cancelled, or parked initiatives are strictly excluded.
4. Returns actionable context: project name, deliverable, task ID, next action, priority, and version.

---

## Optimistic Concurrency Control

Every mutable record contains a monotonic integer `version`.
- Updates must provide the `version` that was read by the client.
- If the file on disk has a different version, the update is rejected with HTTP `409 Conflict`.
- Clients must reload the fresh state and re-apply changes. Never overwrite concurrent updates.

---

## Audit History & Git Commits

### Audit Log (`ops-data/activity/`):
Every operational mutation creates an append-only entry in `ops-data/activity/YYYY-MM.ndjson`:
- Captures: `id`, `occurred_at`, `actor`, `action`, `object_type`, `object_id`, `summary`, `before`, and `after`.

### Git Commits:
Every state-changing API or UI action creates an atomic Git commit:
- Stages the modified canonical JSON file(s), `next_ic.json` (if bumped), the daily snapshot, and the updated activity log.
- Generates a human-readable commit message:
  ```text
  ops: Dax-A claimed TASK-0001
  ops: Cary decided DECISION-0002
  ops: Dax-A created project CommPilot Production (IC-000094)
  ```
- If Git staging or commit fails, a `SyncFailure` (HTTP 503) is raised to alert operators immediately.

### Daily Snapshots (`ops-data/snapshots/`):
A full point-in-time snapshot is generated daily at `ops-data/snapshots/YYYY-MM-DD.json`. The snapshot includes all projects, tasks, decisions, deliverables, workstreams, recent activity, and the current `next_ic` counter.

---

## Git Workflow

- **Stable Main:** The `main` branch is the stable, authoritative operating state.
- **Feature & Documentation Changes:** Substantial codebase or architectural modifications occur on branches and merge via Pull Request.
- **Charlie Integration:** Charlie operates on the `charlie` branch (or topic branches) and enters `main` via PR.
- **Routine Operational Changes:** Routine state mutations executed through the application commit directly to Git on the active deployment.

---

## Definition of Success

1. Cary can open the application and instantly see what needs attention.
2. Dax-A can autonomously query `/api/next`, execute tasks, record blockers, and allocate projects.
3. Every operational change is attributable to an actor and timestamped.
4. The Git repository is always up to date and understandable by Charlie without any database access.
5. The system remains simple, dependable, and free of extraneous infrastructure.
