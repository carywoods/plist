# AGENTS.md

## Purpose

This repository supports an internal operational control system whose primary purpose is to answer:

**What should we work on next?**

The system is not intended to become a generic project-management platform, CRM, idea registry, or collaboration suite.

Keep the application focused on operational state, priorities, ownership, next actions, decisions, and organizational memory.

---

## Repository Context

Repository:

`carywoods/plist`

The repository originally contains operational data files rather than a mature application.

Treat existing files as potentially valuable organizational records.

Do not delete, rename, reorganize, normalize, or rewrite existing data files without first understanding their contents and purpose.

Prefer additive development over destructive restructuring.

Git history is part of the recovery strategy.

---

## Working Environment

The repository may be located on a shared NAS mounted at:

`/mnt/dax/code/plist`

Cary and Dax-A may both have access to the NAS share.

Do not assume:

- local filesystem semantics identical to ext4,
- reliable file locking,
- identical Linux UID/GID values across machines,
- that all writers are operating from the same host,
- or that filesystem ownership reflects NAS authorization.

Avoid designs that depend heavily on local filesystem locks.

The NAS is a shared working and handoff environment.

Git remains the durable versioned record.

---

## Current Actors

### Cary

Cary is the organizational owner and final authority.

Cary may:

- create projects,
- change priorities,
- assign work,
- approve decisions,
- park initiatives,
- archive initiatives,
- approve spending,
- override algorithmic recommendations,
- and modify any operational record.

### Dax-A

Dax-A is a trusted operational agent.

Dax-A should be able to:

- read current operational state,
- create tasks,
- claim tasks,
- update task status,
- mark tasks blocked,
- record blockers,
- complete tasks,
- attach completion notes,
- create follow-up tasks,
- recommend priorities,
- and request decisions from Cary.

Dax-A should not independently make major strategic decisions unless explicitly authorized.

### Charlie

Charlie is ChatGPT and does not currently have direct access to the live operational database or the NAS filesystem.

Charlie may be able to read the Git repository.

Therefore:

**The Git repository must contain a sufficiently complete and current representation of operational state for Charlie to understand what is happening.**

Do not design the system around the assumption that Charlie can query the production database.

---

## Source of Truth

The architecture uses two copies of operational data.

### 1. Live operational database

The live database is the authoritative working state.

Cary and Dax-A may interact with this system directly.

Normal operational updates occur here.

### 2. Git repository mirror

The repository contains a structured, human-readable mirror of the operational state.

The repository serves as:

- organizational memory,
- recovery data,
- an audit-friendly historical record,
- a readable representation for Charlie and other agents,
- and a second copy of important operational information.

The normal synchronization direction is:

**Application/API → Live database → Git mirror**

Do not implement uncontrolled bidirectional synchronization.

Do not allow arbitrary Git edits to silently overwrite live database state.

Repository-to-database restoration or import may be implemented later as an explicit administrative operation.

---

## Core Design Principle

No important organizational state should exist in only one place.

A successful operational change should ultimately exist in:

1. the live database, and
2. the Git-readable mirror.

Git commits provide additional historical protection.

---

## Core Objects

The primary hierarchy is:

**Workstream → Project → Deliverable → Task**

A lightweight Decision object is also required.

---

## Workstream

A Workstream represents an ongoing area of organizational activity.

Examples:

- Publishing
- Teaching
- Infrastructure
- Product Operations
- Marketing
- AI and Model Evaluation

Suggested fields:

- id
- name
- description
- owner
- status
- created_at
- updated_at
- updated_by
- version

---

## Project

A Project represents a defined initiative.

Examples include:

- CommPilot
- OnRamp
- Bioscope
- INT 204
- WSWA 4.5

Suggested fields:

- id
- name
- description
- workstream_id
- status
- owner
- priority
- impact
- urgency
- effort
- last_activity
- last_reviewed
- created_at
- created_by
- updated_at
- updated_by
- version

Suggested statuses:

- active
- incubating
- parked
- blocked
- completed
- archived

---

## Deliverable

A Deliverable represents a concrete outcome.

Suggested fields:

- id
- project_id
- title
- description
- owner
- status
- due_date
- definition_of_done
- created_at
- created_by
- updated_at
- updated_by
- version

---

## Task

A Task represents an executable action.

Suggested fields:

- id
- project_id
- deliverable_id
- title
- description
- owner
- status
- priority
- impact
- urgency
- effort
- requires_cary
- delegatable
- blocked
- blocker_reason
- due_date
- completed_at
- created_at
- created_by
- updated_at
- updated_by
- version

Suggested statuses:

- ready
- claimed
- in_progress
- waiting
- blocked
- completed
- cancelled

---

## Decision

A Decision represents something requiring judgment rather than simple execution.

Suggested fields:

- id
- project_id
- title
- context
- recommendation
- requested_by
- status
- decision
- decided_by
- created_at
- decided_at
- version

---

## Mandatory Active-Project Rule

Every active project must have:

- an owner,
- a next action,
- and a definition of completion.

The application should identify violations automatically.

Examples:

- active project with no owner,
- active project with no next action,
- active project without recent activity,
- overdue next action,
- blocked task without an explanation.

---

## Dashboard

The default interface should answer the operational question quickly.

Primary sections:

### Needs Cary

Items requiring Cary's judgment, approval, direct work, writing, teaching, spending authorization, or strategic decision.

### Staff Queue

Executable work for Dax-A or other staff agents.

### Waiting / Blocked

Tasks and projects that cannot currently proceed.

### Upcoming

Important work likely to become active soon.

### Recently Completed

Recent task and deliverable completions.

### At Risk

Automatically identify conditions such as:

- active project without an owner,
- active project without a next action,
- no activity for 14 days,
- overdue task,
- blocked task older than 7 days.

Do not overload the dashboard.

---

## Priority

Priority scoring may assist decision-making but must never become the final authority.

Possible factors:

- impact
- urgency
- effort
- requires_cary
- delegatable
- blocked

Algorithmic priority is a recommendation.

Cary's manual priority always wins.

---

## Audit History

Important mutations must create append-only history.

Examples:

- Cary changed CommPilot priority from 3 to 1.
- Dax-A completed TASK-0184.
- Cary parked Bioscope.
- Dax-A marked TASK-0211 blocked.
- Cary reassigned a task.

Do not rely solely on current-row state.

Preserve the sequence of meaningful operational changes.

---

## Optimistic Concurrency

Every mutable record should have a version number.

Updates should specify the version being modified.

If the current record has changed since it was read, reject the update rather than silently overwriting a newer change.

Example:

Caller attempts to update version 17.

Database currently contains version 18.

Result:

**Reject and require reload.**

This is especially important because Cary and Dax-A may update operational state independently.

---

## Git Mirror

The Git mirror should remain simple and readable.

Recommended structure:

```text
ops-data/
  workstreams/
  projects/
  deliverables/
  tasks/
  decisions/
  activity/
  snapshots/
```

Examples:

```text
ops-data/projects/PROJECT-001.yaml
ops-data/tasks/TASK-0184.yaml
ops-data/decisions/DEC-0021.yaml
ops-data/activity/2026-10.ndjson
ops-data/snapshots/2026-10-12.json
```

Use stable IDs.

Prefer:

- YAML for individual records,
- NDJSON for append-only event logs,
- JSON for full snapshots.

Generated output should be deterministic.

Avoid unnecessary formatting churn that produces meaningless Git diffs.

---

## Git Commits

Meaningful operational mutations should generate readable commits.

Examples:

```text
ops: Cary changed CommPilot priority 3 -> 1
ops: Dax-A completed TASK-0184
ops: Cary parked Bioscope
ops: Dax-A blocked TASK-0211
```

Do not hide synchronization failures.

If:

- mirror generation fails,
- a file cannot be written,
- Git staging fails,
- Git commit fails,
- or Git synchronization fails,

surface the problem clearly.

Never report successful mirroring unless it actually occurred.

---

## Daily Snapshot

Generate a complete point-in-time snapshot periodically.

Daily is sufficient for the prototype.

Example:

```text
ops-data/snapshots/2026-10-12.json
```

The snapshot should contain enough information to reconstruct operational state if the live database is lost.

---

## API Philosophy

The API must be straightforward enough for trusted agents to use.

Likely endpoints include:

```text
GET    /api/next
GET    /api/projects
GET    /api/projects/:id
GET    /api/tasks
GET    /api/tasks/:id

POST   /api/tasks
PATCH  /api/tasks/:id

POST   /api/tasks/:id/claim
POST   /api/tasks/:id/complete
POST   /api/tasks/:id/block

POST   /api/projects/:id/park

GET    /api/decisions/pending
POST   /api/decisions
```

Do not treat these exact paths as mandatory if the existing framework suggests a cleaner design.

Preserve the capability.

---

## `/api/next`

This is an important endpoint.

A trusted agent should be able to ask:

**What should I work on next?**

A response should include enough context to act.

Example:

```json
{
  "project": "CommPilot",
  "deliverable": "Classifier model review",
  "task_id": "TASK-0184",
  "next_action": "Run production validation",
  "owner": "Dax-A",
  "priority": 1,
  "status": "ready"
}
```

Do not return blocked, completed, cancelled, or unauthorized work as executable work.

---

## Existing Data

Existing repository data must be inspected before changes are made.

Before modifying structures:

1. inventory existing files,
2. identify data formats,
3. identify duplicate or obsolete records,
4. identify fields already being used,
5. identify relationships between files,
6. preserve useful historical information,
7. avoid deleting uncertain records.

When uncertain, preserve rather than destroy.

Create migration tooling where practical.

Do not manually transform large amounts of source data without repeatable logic.

---

## Development Rules

### Keep it simple

Prefer the smallest architecture that satisfies the operational requirement.

Avoid premature infrastructure.

### Do not build a generic project manager

Do not add features simply because Jira, Asana, Monday, Notion, or similar tools have them.

Avoid unless specifically requested:

- Gantt charts,
- chat,
- internal messaging,
- document editing,
- CRM,
- timesheets,
- complex reporting,
- social features,
- elaborate notification systems,
- excessive role systems,
- custom workflow builders.

### Preserve recoverability

A database failure must not destroy organizational state.

### Preserve readability

A knowledgeable human or AI agent should be able to inspect the Git mirror and understand the organization.

### Preserve auditability

Important changes should be attributable to an actor and time.

### Prefer explicit behavior

Avoid hidden automation that silently changes strategic state.

---

## Code Changes

Before making substantial changes:

1. inspect the repository,
2. inspect existing application files if present,
3. inspect package manifests,
4. inspect database configuration,
5. inspect deployment configuration,
6. understand existing conventions.

Do not migrate frameworks merely because another framework is preferred.

Reuse existing architecture where practical.

---

## Testing

At minimum, test:

- create project,
- update project,
- create task,
- claim task,
- block task,
- complete task,
- create decision,
- concurrency conflict,
- Git mirror generation,
- Git activity generation,
- dashboard classification,
- `/api/next`,
- snapshot generation.

Test failure paths as well as success paths.

A mirror failure must not be mistaken for success.

---

## Shared NAS Safety

Because the working tree may live on a CIFS/SMB-mounted NAS:

- do not assume file ownership reflects actual authorization,
- avoid relying on chmod/chown as an application-security mechanism,
- do not store secrets in repository files,
- avoid brittle filesystem locking,
- write generated files atomically where practical,
- expect occasional network or filesystem latency,
- handle failed writes safely,
- do not corrupt an existing mirror file if generation fails halfway through.

Prefer:

1. write temporary file,
2. validate content,
3. atomically replace target where supported.

---

## Secrets

Never commit:

- passwords,
- API keys,
- NAS credentials,
- GitHub tokens,
- database passwords,
- authentication secrets.

Use environment variables or appropriate secret-management mechanisms.

Provide `.env.example` files containing variable names only.

---

## Git Workflow

Development may occur directly on `main` unless Cary explicitly requests a branch.

Do not create branches solely because conventional software workflows normally use them.

This repository is small and Git history already provides rollback.

Before destructive or large-scale structural changes, create a checkpoint commit.

Use clear commit messages.

---

## Definition of Prototype Success

The prototype is successful if:

1. Cary can open the application and quickly understand what needs attention.
2. Cary can change organizational state.
3. Dax-A can safely execute and update assigned work.
4. Every important change is attributable.
5. Current state is mirrored to Git.
6. Charlie can inspect the repository and understand current operational state without database access.
7. A database failure does not erase the organization's operational history.
8. The system remains small enough for one person and a small digital staff to maintain.

The goal is not feature completeness.

The goal is a dependable operational control system.
