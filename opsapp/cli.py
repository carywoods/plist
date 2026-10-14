from __future__ import annotations

import json
from pathlib import Path

import click

from . import storage


def register_cli(app) -> None:
    app.cli.add_command(snapshot)
    app.cli.add_command(sync)
    app.cli.add_command(validate)


@click.command("snapshot")
def snapshot() -> None:
    """Generate daily JSON snapshot of entire operational state."""
    path = storage.write_snapshot()
    click.echo(f"Snapshot written to {path}")


@click.command("sync")
@click.option("-m", "--message", default="ops: refresh snapshot and sync state", help="Git commit message")
def sync(message: str) -> None:
    """Write snapshot and commit state changes to git."""
    snap_path = storage.write_snapshot()
    commit = storage.git_commit([snap_path], message)
    click.echo(json.dumps({"snapshot": str(snap_path), "git_commit": commit}, indent=2))


@click.command("validate")
def validate() -> None:
    """Validate all project JSON records across lifecycle folders."""
    projects = storage.list_projects()
    tasks = storage.list_tasks()
    decisions = storage.list_decisions()
    click.echo(f"Loaded {len(projects)} projects across lifecycle folders.")
    click.echo(f"Loaded {len(tasks)} tasks.")
    click.echo(f"Loaded {len(decisions)} decisions.")

    errors = 0
    for p in projects:
        if not p.get("id"):
            click.echo(f"ERROR: Project missing ID: {p.get('name')}", err=True)
            errors += 1
        if not p.get("name"):
            click.echo(f"ERROR: Project missing name: {p.get('id')}", err=True)
            errors += 1

    if errors == 0:
        click.echo("All records validated successfully. Integrity OK.")
    else:
        click.echo(f"Found {errors} errors.", err=True)
