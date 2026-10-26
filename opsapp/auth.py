"""Single shared password authentication for plist.

Cary's decision (2026-10-13): one shared password for Cary and Dax-A, no user
accounts, no roles. ``X-Actor`` stays attribution-only, exactly as before.

Accepted credentials:
  * session cookie created by ``POST /login`` (lifetime ``OPS_SESSION_DAYS``)
  * ``X-Ops-Key: <password>`` header for agents, curl and cron
  * HTTP Basic (``curl -u cary:<password>``)

Authentication is enforced whenever ``OPS_PASSWORD`` is configured. Without it
the app refuses to start outside ``TESTING`` (see ``opsapp/__init__.py``), so a
deployment cannot silently serve an unprotected board.
"""

from __future__ import annotations

import hmac
import time

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

bp = Blueprint("auth", __name__)

SESSION_KEY = "authenticated"
PUBLIC_ENDPOINTS = {"auth.login", "auth.logout", "static", "ops.health"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def auth_enabled() -> bool:
    return bool(current_app.config.get("OPS_PASSWORD"))


def _matches(candidate: str | None) -> bool:
    expected = current_app.config.get("OPS_PASSWORD") or ""
    if not expected or not candidate:
        return False
    return hmac.compare_digest(str(candidate), str(expected))


def _host_of(value: str | None) -> str:
    if not value:
        return ""
    value = value.split(",")[0].strip()
    return value.split("//", 1)[-1].split("/")[0].split(":")[0].lower()


def same_origin_ok() -> bool:
    """Cheap CSRF cover: a browser-sent Origin must point at this host."""
    if request.method in SAFE_METHODS:
        return True
    origin = request.headers.get("Origin")
    if not origin:
        return True
    allowed = {
        _host_of(request.host),
        _host_of(request.headers.get("X-Forwarded-Host")),
        _host_of(current_app.config.get("OPS_PUBLIC_HOST")),
    }
    allowed.discard("")
    return _host_of(origin) in allowed


def _credentials_ok() -> bool:
    if session.get(SESSION_KEY):
        return True
    if _matches(request.headers.get("X-Ops-Key")):
        return True
    basic = request.authorization
    if basic and basic.type == "basic" and _matches(basic.password):
        return True
    return False


@bp.before_app_request
def guard():
    if not auth_enabled():
        return None
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not _credentials_ok():
        if request.path.startswith("/api/"):
            return jsonify({"error": "authentication required", "hint": "X-Ops-Key header or session cookie"}), 401
        return redirect(url_for("auth.login", next=request.path))
    if not same_origin_ok():
        return jsonify({"error": "cross-origin write refused"}), 403
    return None


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("ops.dashboard_page")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled():
        return redirect(url_for("ops.dashboard_page"))
    nxt = _safe_next(request.values.get("next"))
    if request.method == "POST":
        supplied = request.form.get("password", "")
        if _matches(supplied):
            session.clear()
            session[SESSION_KEY] = True
            session.permanent = True
            return redirect(nxt)
        time.sleep(0.3)
        return render_template("login.html", error="Invalid password", next=nxt), 401
    return render_template("login.html", error=None, next=nxt)


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
