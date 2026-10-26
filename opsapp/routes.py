from __future__ import annotations

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from . import service, storage

bp = Blueprint("ops", __name__)


def payload() -> dict:
    return dict(request.get_json(silent=True) or request.form)


def actor(data: dict | None = None) -> str:
    data = data or {}
    return request.headers.get("X-Actor") or data.pop("actor", None) or "Dax-A"


def response(result: tuple[dict, dict], status: int = 200):
    record, sync = result
    return jsonify({"data": record, "sync": sync}), status


@bp.errorhandler(service.OpsError)
def ops_error(exc):
    if request.path.startswith("/api/"):
        return jsonify({"error": str(exc), **exc.details}), exc.status_code
    flash(str(exc), "error")
    return redirect(request.referrer or url_for("ops.dashboard_page")), exc.status_code


@bp.get("/health")
def health():
    return jsonify({"status": "ok", "service": "plist-ops"})


@bp.get("/")
def dashboard_page():
    return render_template("dashboard.html", board=service.dashboard())


@bp.get("/projects")
def projects_page():
    return render_template("projects.html", projects=service.list_records("projects"), workstreams=service.list_records("workstreams"))


@bp.get("/projects/<object_id>")
def project_page(object_id):
    project = service._get("projects", object_id)
    return render_template(
        "project.html", project=project,
        deliverables=service.list_records("deliverables", "project_id = ?", (object_id,)),
        tasks=service.list_records("tasks", "project_id = ?", (object_id,)),
        decisions=service.list_records("decisions", "project_id = ?", (object_id,)),
    )


@bp.get("/tasks")
def tasks_page():
    return render_template("tasks.html", tasks=service.list_records("tasks"), projects=service.list_records("projects"))


@bp.get("/decisions")
def decisions_page():
    return render_template("decisions.html", decisions=service.list_records("decisions"), projects=service.list_records("projects"))


@bp.get("/activity")
def activity_page():
    rows = storage.list_activity(limit=200)
    return render_template("activity.html", activity=rows, failures=[])


@bp.post("/ui/tasks/<object_id>/<action>")
def task_action_ui(object_id, action):
    data = payload()
    service.task_action(object_id, action, data, actor(data))
    flash(f"{object_id} {action} succeeded", "success")
    return redirect(request.referrer or url_for("ops.tasks_page"))


@bp.post("/ui/tasks")
def create_task_ui():
    data = payload()
    record, _sync = service.create_task(data, actor(data))
    flash(f"Created {record['id']}", "success")
    return redirect(url_for("ops.tasks_page"))


@bp.post("/ui/projects")
def create_project_ui():
    data = payload()
    record, _sync = service.create_project(data, actor(data))
    flash(f"Created {record['id']}", "success")
    return redirect(url_for("ops.project_page", object_id=record["id"]))


@bp.post("/ui/projects/<object_id>")
def update_project_ui(object_id):
    data = payload()
    service.update_record("project", object_id, data, actor(data))
    flash(f"Updated {object_id}", "success")
    return redirect(url_for("ops.project_page", object_id=object_id))


@bp.post("/ui/deliverables")
def create_deliverable_ui():
    data = payload()
    record, _sync = service.create_deliverable(data, actor(data))
    flash(f"Created {record['id']}", "success")
    return redirect(url_for("ops.project_page", object_id=record["project_id"]))


@bp.post("/ui/decisions")
def create_decision_ui():
    data = payload()
    record, _sync = service.create_decision(data, actor(data))
    flash(f"Requested {record['id']}", "success")
    return redirect(url_for("ops.decisions_page"))


@bp.post("/ui/decisions/<object_id>/decide")
def decide_ui(object_id):
    data = payload()
    service.decide(object_id, data, actor(data))
    flash(f"Recorded {object_id}", "success")
    return redirect(url_for("ops.decisions_page"))


@bp.get("/api/next")
def api_next():
    item = service.next_task(request.args.get("owner"))
    if item is None:
        return jsonify({"data": None, "message": "No executable ready task matches"}), 404
    return jsonify(item)


@bp.get("/api/workstreams")
def api_workstreams():
    return jsonify({"data": service.list_records("workstreams")})


@bp.post("/api/workstreams")
def api_create_workstream():
    data = payload()
    return response(service.create_workstream(data, actor(data)), 201)


@bp.get("/api/projects")
def api_projects():
    return jsonify({"data": service.list_records("projects")})


@bp.post("/api/projects")
def api_create_project():
    data = payload()
    return response(service.create_project(data, actor(data)), 201)


@bp.get("/api/projects/<object_id>")
def api_project(object_id):
    return jsonify({"data": service._get("projects", object_id)})


@bp.patch("/api/projects/<object_id>")
def api_update_project(object_id):
    data = payload()
    return response(service.update_record("project", object_id, data, actor(data)))


@bp.post("/api/projects/<object_id>/park")
def api_park_project(object_id):
    data = payload()
    return response(service.park_project(object_id, data, actor(data)))


@bp.get("/api/deliverables")
def api_deliverables():
    return jsonify({"data": service.list_records("deliverables")})


@bp.post("/api/deliverables")
def api_create_deliverable():
    data = payload()
    return response(service.create_deliverable(data, actor(data)), 201)


@bp.get("/api/tasks")
def api_tasks():
    where, params = "", ()
    if request.args.get("status"):
        where, params = "status = ?", (request.args["status"],)
    return jsonify({"data": service.list_records("tasks", where, params)})


@bp.post("/api/tasks")
def api_create_task():
    data = payload()
    return response(service.create_task(data, actor(data)), 201)


@bp.get("/api/tasks/<object_id>")
def api_task(object_id):
    return jsonify({"data": service._get("tasks", object_id)})


@bp.patch("/api/tasks/<object_id>")
def api_update_task(object_id):
    data = payload()
    return response(service.update_record("task", object_id, data, actor(data)))


@bp.post("/api/tasks/<object_id>/<action>")
def api_task_action(object_id, action):
    if action not in {"claim", "block", "complete"}:
        raise service.NotFound("Unknown task action")
    data = payload()
    return response(service.task_action(object_id, action, data, actor(data)))


@bp.get("/api/decisions/pending")
def api_pending_decisions():
    return jsonify({"data": service.list_records("decisions", "status = 'pending'")})


@bp.post("/api/decisions")
def api_create_decision():
    data = payload()
    return response(service.create_decision(data, actor(data)), 201)


@bp.post("/api/decisions/<object_id>/decide")
def decide_ui_api(object_id):
    data = payload()
    return response(service.decide(object_id, data, actor(data)))


@bp.post("/api/admin/sync")
def api_sync():
    data = payload()
    service.actor_allowed(actor(data), cary_only=True)
    snapshot_path = storage.write_snapshot()
    commit = storage.git_commit([snapshot_path], "ops: Cary requested manual snapshot sync")
    return jsonify({"sync": {"mirrored": True, "git_commit": commit, "snapshot": str(snapshot_path)}})


@bp.get("/api/dashboard")
def api_dashboard():
    return jsonify(service.dashboard())
