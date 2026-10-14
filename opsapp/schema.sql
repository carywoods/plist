PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS counters (
  kind TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS workstreams (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  owner TEXT, status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  workstream_id TEXT REFERENCES workstreams(id), status TEXT NOT NULL,
  owner TEXT, priority INTEGER, impact INTEGER, urgency INTEGER, effort INTEGER,
  completion_definition TEXT NOT NULL DEFAULT '', last_activity TEXT, last_reviewed TEXT,
  legacy_source TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS deliverables (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', owner TEXT,
  status TEXT NOT NULL, priority INTEGER, impact INTEGER, urgency INTEGER, effort INTEGER,
  due_date TEXT, definition_of_done TEXT NOT NULL DEFAULT '', completed_at TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  deliverable_id TEXT REFERENCES deliverables(id), title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', owner TEXT, status TEXT NOT NULL,
  priority INTEGER, impact INTEGER, urgency INTEGER, effort INTEGER,
  requires_cary INTEGER NOT NULL DEFAULT 0, delegatable INTEGER NOT NULL DEFAULT 1,
  blocked INTEGER NOT NULL DEFAULT 0, blocker_reason TEXT,
  due_date TEXT, completed_at TEXT, completion_notes TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), title TEXT NOT NULL,
  context TEXT NOT NULL DEFAULT '', recommendation TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending', requested_by TEXT NOT NULL,
  decided_by TEXT, decision TEXT, decided_at TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
  summary TEXT NOT NULL, before_json TEXT, after_json TEXT
);

CREATE TABLE IF NOT EXISTS sync_failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
  object_type TEXT NOT NULL, object_id TEXT NOT NULL, error TEXT NOT NULL,
  resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_queue ON tasks(status, owner, priority);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_activity_time ON activity(occurred_at DESC);
