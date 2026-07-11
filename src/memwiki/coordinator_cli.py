from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from memwiki.coordinator_api import CoordinatorAPI

coordinator_app = typer.Typer(no_args_is_help=True)


def _api() -> CoordinatorAPI:
    from memwiki.cli import state

    return CoordinatorAPI(state.wiki)


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, sort_keys=True))


def _invoke(operation: str, function: object) -> None:
    try:
        result = function()  # type: ignore[operator]
    except Exception as exc:
        _emit({"error": str(exc), "operation": operation})
        raise typer.Exit(code=1) from exc
    _emit(result)


@coordinator_app.command("initialize")
def initialize(
    run_id: str = typer.Option(..., "--run-id"),
    max_workers: int = typer.Option(6, "--max-workers", min=1, max=6),
) -> None:
    _invoke("initialize", lambda: _api().initialize(run_id=run_id, max_workers=max_workers))


@coordinator_app.command("status")
def status() -> None:
    _invoke("status", _api().status)


@coordinator_app.command("tick")
def tick() -> None:
    _invoke("tick", _api().tick)


@coordinator_app.command("run")
def run(max_ticks: Optional[int] = typer.Option(None, "--max-ticks", min=1)) -> None:
    _invoke("run", lambda: _api().run(max_ticks=max_ticks))


@coordinator_app.command("stop")
def stop(reason: str = typer.Option("operator request", "--reason")) -> None:
    _invoke("stop", lambda: _api().stop(reason=reason))


@coordinator_app.command("resume")
def resume() -> None:
    _invoke("resume", _api().resume)


@coordinator_app.command("verify")
def verify() -> None:
    _invoke("verify", _api().verify)


@coordinator_app.command("migrate")
def migrate() -> None:
    _invoke("migrate", _api().migrate)


@coordinator_app.command("render")
def render(output: Optional[Path] = typer.Option(None, "--output", "-o")) -> None:
    _invoke("render", lambda: _api().render(output=output))


__all__ = ["coordinator_app"]
