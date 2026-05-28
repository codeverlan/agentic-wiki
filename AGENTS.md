# Memwiki Agent Instructions

This repository implements `memwiki`, an HTML-first local memory wiki.

Required workflow:

- Use tests before behavior changes.
- Keep canonical memory pages as semantic HTML with JSON-LD metadata.
- Never write generated knowledge directly into canonical `wiki/` output in a workspace; use `drafts/<draft-id>/`.
- Preserve claim-level provenance for every substantive claim.
- Keep raw source files immutable after ingest.
- Run `uv run pytest`, `uv run ruff check .`, `uv run mypy src`, and docs checks before claiming completion.
- Update generated documentation whenever commands, schemas, workflows, storage layout, or validation behavior changes.
- Use `memwiki docs draft` for documentation updates in workspaces, then promote through the draft workflow.
- Treat `MemwikiWorkspace` in `src/memwiki/api.py` as the public integration surface.
- Keep CLI commands as thin wrappers around the public API.
- Preserve machine-readable JSON outputs for agent-facing commands.
- Keep `.memwiki/agent-capabilities.json` aligned with supported commands and mutation policy.
