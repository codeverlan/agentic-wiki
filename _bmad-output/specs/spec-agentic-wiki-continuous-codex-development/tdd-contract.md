# TDD Contract

## Rule

Use tests before behavior changes. For non-code documentation-only edits, use docs checks or source consistency checks as the equivalent first proof.

## Red

Before production edits, create one of:

- A failing unit or integration test.
- A characterization test that locks current behavior before refactor.
- A docs drift check that fails before generated documentation is updated.
- A capability manifest assertion that exposes advertised-contract drift.
- An incident-log renderer/API/CLI test when changing incident, exception, PHI-monitoring, or no-sensitive-values behavior.

## Green

Make the smallest production change that passes the focused test while preserving:

- Semantic HTML with JSON-LD for canonical pages.
- Draft output before promotion into `wiki/`.
- Claim-level provenance.
- Raw source immutability after ingest.
- Canonical `agentic_wiki` and compatibility `memwiki` behavior.
- Synthetic-only clinical test data.

## Refactor

Refactor only after focused tests pass. Keep refactors local to the file group unless the coordinator approves cross-group changes.

## Required Validation

Run before claiming completion:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

Run docs checks when commands, schemas, workflows, storage layout, validation behavior, or generated documentation changes:

```bash
uv run agentic-wiki docs check
```

Run compatibility docs checks when legacy command behavior changes:

```bash
uv run memwiki docs check
```

## Clinical Test Rules

- Use synthetic fixtures only.
- Do not include actual PHI in tests, examples, prompts, screenshots, logs, or docs.
- Assert `OperationContext` requirements for PHI-reading or mutating operations.
- Assert blocked export behavior unless the caller explicitly marks output deidentified or synthetic.
- For PHI-aware incident logs, assert metadata-only evidence and rejection of PHI, credential, token, password, or raw secret value fields.
