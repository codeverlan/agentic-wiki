from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from memwiki import MemwikiWorkspace
from memwiki.linter import render_lint_errors

app = typer.Typer(no_args_is_help=True)
docs_app = typer.Typer(no_args_is_help=True)
export_app = typer.Typer(no_args_is_help=True)
app.add_typer(docs_app, name="docs")
app.add_typer(export_app, name="export")


class CliState:
    wiki: MemwikiWorkspace


state = CliState()


@app.callback()
def main(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        "-w",
        help="Memwiki workspace root.",
        resolve_path=True,
    )
) -> None:
    state.wiki = MemwikiWorkspace(workspace)


def _json(data: object) -> None:
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    typer.echo(json.dumps(data, sort_keys=True))


def _fail(message: str) -> None:
    typer.echo(message, err=False)
    raise typer.Exit(code=1)


@app.command()
def init() -> None:
    _json(state.wiki.init())


@app.command()
def ingest(
    path: Path,
    alias: Optional[str] = typer.Option(None, "--alias"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview ingest without writing files."),
) -> None:
    try:
        result = state.wiki.ingest(path, alias=alias, dry_run=dry_run)
    except Exception as exc:
        _fail(str(exc))
    _json(result)


@app.command("compile")
def compile_command(source_id: str) -> None:
    try:
        result = state.wiki.compile(source_id)
    except Exception as exc:
        _fail(str(exc))
    _json(result)


@app.command()
def promote(
    draft_id: str,
    check_only: bool = typer.Option(False, "--check-only", help="Validate without copying into canonical wiki."),
) -> None:
    try:
        result = state.wiki.promote(draft_id, check_only=check_only)
    except Exception as exc:
        _fail(str(exc))
    _json(result)


@app.command()
def lint() -> None:
    try:
        result = state.wiki.lint()
    except Exception as exc:
        _fail(str(exc))
    if not result.ok:
        _fail(render_lint_errors(result.errors))
    typer.echo("OK")


@app.command()
def query(
    question: str,
    draft_page: bool = typer.Option(False, "--draft-page"),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    try:
        result = state.wiki.query(question, draft_page=draft_page)
    except Exception as exc:
        _fail(str(exc))
    if json_output:
        _json(result)
    else:
        typer.echo(result.answer)


@app.command()
def resolve(object_id: str) -> None:
    try:
        _json(state.wiki.resolve(object_id))
    except Exception as exc:
        _fail(str(exc))


@app.command()
def backlinks(object_id: str) -> None:
    try:
        _json({"object_id": object_id, "backlinks": state.wiki.backlinks(object_id)})
    except Exception as exc:
        _fail(str(exc))


@app.command()
def capabilities() -> None:
    _json(state.wiki.capabilities())


@docs_app.command("check")
def docs_check() -> None:
    try:
        status = state.wiki.docs_check()
    except Exception as exc:
        _fail(str(exc))
    if not status.clean:
        _fail("Stale documentation: " + ", ".join(status.stale_paths))
    typer.echo("OK")


@docs_app.command("draft")
def docs_draft() -> None:
    try:
        result = state.wiki.docs_draft()
    except Exception as exc:
        _fail(str(exc))
    _json(result)


@export_app.command("static")
def export_static_command(output: Path = typer.Option(Path("site"), "--output", "-o")) -> None:
    try:
        result = state.wiki.export_static(output)
    except Exception as exc:
        _fail(str(exc))
    _json(result)
