from __future__ import annotations

import json

import click

from . import mirror
from .db import ensure_schema, seed_demo_if_empty
from .legacy import import_legacy_projects


def register_cli(app) -> None:
    app.cli.add_command(init_db)
    app.cli.add_command(snapshot)
    app.cli.add_command(sync_mirror)
    app.cli.add_command(import_legacy)


@click.command("init-db")
@click.option("--no-seed", is_flag=True, help="Create tables without demo records")
def init_db(no_seed: bool) -> None:
    ensure_schema()
    if not no_seed:
        seed_demo_if_empty()
    click.echo("Database initialized. Run `flask sync-mirror` to create the repository mirror.")


@click.command("snapshot")
def snapshot() -> None:
    click.echo(mirror.write_snapshot())


@click.command("sync-mirror")
def sync_mirror() -> None:
    result = mirror.sync_all()
    click.echo(json.dumps(result, indent=2))


@click.command("import-legacy")
@click.option("--dry-run", is_flag=True, help="Inspect without writing")
def import_legacy(dry_run: bool) -> None:
    """Idempotently import canonical lifecycle JSON without changing sources."""
    result = import_legacy_projects(dry_run=dry_run)
    if result["imported"] and not dry_run:
        result["sync"] = mirror.sync_all("ops: Cary imported repository project registry")
    click.echo(json.dumps(result, indent=2))
