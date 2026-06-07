# Agentic Wiki Agent Instructions

This repository implements Agentic Wiki, an HTML-first local memory wiki with a compatibility `memwiki` package/CLI surface.

Required workflow:

- Use tests before behavior changes.
- Keep canonical memory pages as semantic HTML with JSON-LD metadata.
- Never write generated knowledge directly into canonical `wiki/` output in a workspace; use `drafts/<draft-id>/`.
- Preserve claim-level provenance for every substantive claim.
- Keep raw source files immutable after ingest.
- Run `uv run pytest`, `uv run ruff check .`, `uv run mypy src`, and docs checks before claiming completion.
- Update generated documentation whenever commands, schemas, workflows, storage layout, or validation behavior changes.
- Use `agentic-wiki docs draft` for documentation updates in workspaces, then promote through the draft workflow.
- Treat `AgenticWikiWorkspace` from `agentic_wiki` as the canonical public integration surface.
- Keep `memwiki.MemwikiWorkspace` as a compatibility shim unless the user explicitly approves removing it.
- Keep CLI commands as thin wrappers around the public API.
- Preserve machine-readable JSON outputs for agent-facing commands.
- Keep `.memwiki/agent-capabilities.json` aligned with supported commands and mutation policy.
- For clinical PHI workspaces, require `OperationContext` on PHI-reading or mutating operations.
- Do not use actual PHI in tests, examples, prompts, screenshots, logs, or documentation.
- Keep clinical PHI workspaces strict local-only: no remote model adapters, no cloud PHI storage, no telemetry, and no static PHI export unless explicitly deidentified/synthetic.
- Treat one clinical PHI workspace as one pseudonymous client record.
- The canonical GitHub repo is `codeverlan/agentic-wiki`, pushed with remote `github-personal:codeverlan/agentic-wiki.git`.
- The installed Codex skill lives at `/Users/tyler-lcsw/.codex/skills/agentic-wiki-planner`.
- Keep the project symlink `skills/agentic-wiki-planner` pointing to the installed skill; do not move the installed skill into the repo.
- Skill-link synchronization belongs on branch `skill/agentic-wiki-planner` unless the user asks for a different branch.
