from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from agentic_wiki import AgenticWikiWorkspace, OperationContext
from memwiki.linter import render_lint_errors

app = typer.Typer(no_args_is_help=True)
docs_app = typer.Typer(no_args_is_help=True)
export_app = typer.Typer(no_args_is_help=True)
app.add_typer(docs_app, name="docs")
app.add_typer(export_app, name="export")


class CliState:
    wiki: AgenticWikiWorkspace
    actor_id: Optional[str] = None
    actor_role: Optional[str] = None
    purpose_of_use: Optional[str] = None
    session_id: Optional[str] = None
    reason: Optional[str] = None


state = CliState()


@app.callback()
def main(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        "-w",
        help="Agentic Wiki workspace root.",
        resolve_path=True,
    ),
    actor_id: Optional[str] = typer.Option(None, "--actor-id", help="Authenticated actor ID for PHI workspaces."),
    actor_role: Optional[str] = typer.Option(None, "--actor-role", help="Authenticated actor role."),
    purpose_of_use: Optional[str] = typer.Option(None, "--purpose-of-use", help="Purpose of use, such as treatment."),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Authenticated host-application session ID."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Optional operation reason for audit logs."),
) -> None:
    state.wiki = AgenticWikiWorkspace(workspace)
    state.actor_id = actor_id
    state.actor_role = actor_role
    state.purpose_of_use = purpose_of_use
    state.session_id = session_id
    state.reason = reason


def _json(data: object) -> None:
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    typer.echo(json.dumps(data, sort_keys=True))


def _fail(message: str) -> None:
    typer.echo(message, err=False)
    raise typer.Exit(code=1)


def _context() -> Optional[OperationContext]:
    values = [state.actor_id, state.actor_role, state.purpose_of_use, state.session_id]
    if not any(values) and state.reason is None:
        return None
    if not all(values):
        raise ValueError("--actor-id, --actor-role, --purpose-of-use, and --session-id must be provided together")
    return OperationContext(
        actor_id=str(state.actor_id),
        actor_role=str(state.actor_role),
        purpose_of_use=str(state.purpose_of_use),
        session_id=str(state.session_id),
        reason=state.reason,
    )


@app.command()
def init(
    profile: str = typer.Option("standard", "--profile", help="Workspace profile: standard or clinical-phi."),
    client_record_id: Optional[str] = typer.Option(None, "--client-record-id", help="Pseudonymous client record ID."),
    attest_local_encryption: bool = typer.Option(
        False,
        "--attest-local-encryption",
        help="Record operator attestation that the local workspace is on encrypted storage.",
    ),
) -> None:
    _json(
        state.wiki.init(
            profile=profile,
            client_record_id=client_record_id,
            local_encrypted_storage_attested=attest_local_encryption,
        )
    )


@app.command()
def ingest(
    path: Path,
    alias: Optional[str] = typer.Option(None, "--alias"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview ingest without writing files."),
    source_category: Optional[str] = typer.Option(None, "--source-category"),
) -> None:
    try:
        result = state.wiki.ingest(
            path,
            alias=alias,
            dry_run=dry_run,
            source_category=source_category,
            context=_context(),
        )
    except Exception as exc:
        _fail(str(exc))
    _json(result)


@app.command("compile")
def compile_command(source_id: str) -> None:
    try:
        result = state.wiki.compile(source_id, context=_context())
    except Exception as exc:
        _fail(str(exc))
    _json(result)


@app.command()
def promote(
    draft_id: str,
    check_only: bool = typer.Option(False, "--check-only", help="Validate without copying into canonical wiki."),
) -> None:
    try:
        result = state.wiki.promote(draft_id, check_only=check_only, context=_context())
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
        result = state.wiki.query(question, draft_page=draft_page, context=_context())
    except Exception as exc:
        _fail(str(exc))
    if json_output:
        _json(result)
    else:
        typer.echo(result.answer)


@app.command()
def resolve(object_id: str) -> None:
    try:
        _json(state.wiki.resolve(object_id, context=_context()))
    except Exception as exc:
        _fail(str(exc))


@app.command()
def backlinks(object_id: str) -> None:
    try:
        _json({"object_id": object_id, "backlinks": state.wiki.backlinks(object_id, context=_context())})
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
        result = state.wiki.docs_draft(context=_context())
    except Exception as exc:
        _fail(str(exc))
    _json(result)


@export_app.command("static")
def export_static_command(
    output: Path = typer.Option(Path("site"), "--output", "-o"),
    deidentified: bool = typer.Option(
        False,
        "--deidentified",
        help="Assert exported clinical content is deidentified.",
    ),
) -> None:
    try:
        result = state.wiki.export_static(output, context=_context(), deidentified=deidentified)
    except Exception as exc:
        _fail(str(exc))
    _json(result)
