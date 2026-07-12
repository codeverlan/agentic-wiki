from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from memwiki.coordinator_api import CoordinatorAPI
from memwiki.coordinator_commands import CoordinatorCommandGateway
from memwiki.coordinator_completion import evaluate_completion

coordinator_app = typer.Typer(no_args_is_help=True)


def _api() -> CoordinatorAPI:
    from memwiki.cli import state

    return CoordinatorAPI(state.wiki)


def _gateway() -> CoordinatorCommandGateway:
    from memwiki.cli import state

    return CoordinatorCommandGateway(state.wiki.root)


def _project_json(path: Path) -> dict[str, object]:
    from memwiki.cli import state

    root = state.wiki.root.resolve()
    target = path.expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError("coordinator input must remain inside the workspace") from None
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("coordinator input must contain a JSON object")
    return value


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


@coordinator_app.command("append-event")
def append_event(
    run_id: str = typer.Option(..., "--run-id"),
    event_type: str = typer.Option(..., "--event-type"),
    payload_json: str = typer.Option(..., "--payload-json"),
    actor_id: str = typer.Option(..., "--actor-id"),
    idempotency_key: str = typer.Option(..., "--idempotency-key"),
    actor_type: str = typer.Option("coordinator", "--actor-type"),
    occurred_at: Optional[str] = typer.Option(None, "--occurred-at"),
) -> None:
    def command() -> object:
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise ValueError("payload-json must contain an object")
        return _gateway().append(
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            actor={"type": actor_type, "id": actor_id},
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        ).to_dict()

    _invoke("append-event", command)


@coordinator_app.command("projection")
def projection(run_id: str = typer.Option(..., "--run-id")) -> None:
    _invoke("projection", lambda: _gateway().status(run_id=run_id).to_dict())


@coordinator_app.command("completion-evaluate")
def completion_evaluate(evidence: Path = typer.Option(..., "--evidence")) -> None:
    def evaluate() -> object:
        result = evaluate_completion(_project_json(evidence))
        return {
            "complete": result.complete,
            "disposition": result.disposition,
            "advisory_ids": list(result.advisory_ids),
            "failed_conditions": list(result.failed_conditions),
            "conditions": [condition.to_dict() for condition in result.conditions],
        }

    _invoke("completion-evaluate", evaluate)


__all__ = ["coordinator_app"]
