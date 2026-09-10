"""CLI entry-point for maintainer repair acceptance."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from rincode.maintenance.acceptance import (
    MaintenanceConfigError,
    accept_maintenance_task,
    load_task_manifest,
    resolve_report_path,
    write_acceptance_report,
)

maintenance_app = typer.Typer(
    name="maintain",
    help="Verify a fixed maintainer repair task in disposable local copies.",
    no_args_is_help=True,
)


@maintenance_app.command("accept")
def accept(
    manifest: Path = typer.Option(..., "--manifest", help="Maintainer-owned JSON task manifest."),
    candidate: Path = typer.Option(..., "--candidate", help="Candidate worktree or directory to evaluate."),
    report: Path | None = typer.Option(
        None,
        "--report",
        help="Path for the machine-readable acceptance report.",
    ),
) -> None:
    """Run baseline then candidate and write an independently checkable report."""

    try:
        task = load_task_manifest(manifest)
        output = resolve_report_path(
            report,
            manifest=manifest,
            workspace=task.workspace,
            candidate=candidate,
        )
        result = accept_maintenance_task(task, candidate)
    except MaintenanceConfigError as exc:
        typer.echo(f"invalid maintenance manifest: {exc}", err=True)
        raise typer.Exit(2) from exc

    write_acceptance_report(output, result)
    typer.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise typer.Exit(0 if result["accepted"] else 1)


def register(app: typer.Typer) -> None:
    """Attach ``rincode maintain`` to the public CLI."""

    app.add_typer(maintenance_app, name="maintain")
