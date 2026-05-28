# Memwiki

Memwiki is an HTML-first, local-first memory wiki engine. It turns raw sources
into a plain-file knowledge base with semantic HTML pages, JSONL manifests,
claim-level provenance, draft promotion, validation, and self-updating
documentation checks.

The package is intended to be embedded in other programs or operated by coding
agents. The CLI is a thin wrapper around the public Python API.

## Install

```bash
uv sync
uv run memwiki --help
```

## Quick Start

```bash
uv run memwiki --workspace my-wiki init
uv run memwiki --workspace my-wiki ingest path/to/source.html
uv run memwiki --workspace my-wiki compile <source-id>
uv run memwiki --workspace my-wiki promote <draft-id>
uv run memwiki --workspace my-wiki lint
uv run memwiki --workspace my-wiki query "what does the source say?"
uv run memwiki --workspace my-wiki export static --output my-site
```

## Library API

```python
from memwiki import MemwikiWorkspace

wiki = MemwikiWorkspace("my-wiki")
wiki.init()
source = wiki.ingest("path/to/source.html")
draft = wiki.compile(source.source_id)
wiki.promote(draft.draft_id)
answer = wiki.query("what does the source say?")
```

Agent-oriented helpers:

```python
preview = wiki.ingest("path/to/source.pdf", dry_run=True)
check = wiki.promote("draft_id", check_only=True)
claim = wiki.resolve("claim_abc123")
backlinks = wiki.backlinks("claim_abc123")
capabilities = wiki.capabilities()
```

The same affordances are exposed through the CLI:

```bash
uv run memwiki --workspace my-wiki ingest file.pdf --dry-run
uv run memwiki --workspace my-wiki promote draft_id --check-only
uv run memwiki --workspace my-wiki query "source-backed claims" --json
uv run memwiki --workspace my-wiki resolve claim_abc123
uv run memwiki --workspace my-wiki backlinks claim_abc123
uv run memwiki --workspace my-wiki capabilities
```

## Core Principles

- Raw sources are immutable after ingest.
- Canonical memory is semantic HTML under `wiki/`.
- LLM/model output lands in `drafts/` first.
- Promotion requires validation.
- Every accepted claim must retain source provenance.
- Documentation changes are generated as drafts and promoted like wiki changes.
- Remote model adapters are disabled unless explicitly configured.
- Integration APIs return structured, JSON-serializable result objects.

## Storage Layout

- `raw/`: immutable source copies.
- `wiki/`: accepted canonical HTML pages.
- `drafts/`: proposed wiki and documentation updates.
- `manifests/`: JSONL registries for sources, pages, claims, links, and events.
- `schemas/`: JSON Schemas for manifest records.
- `docs/`: generated architecture, schema, operations, and CLI reference docs.
- `.memwiki/`: config, extracted text artifacts, and generated indexes.
- `.memwiki/agent-capabilities.json`: machine-readable tool contract for coding agents.

## Repository and Skill

The canonical GitHub repository is
[`codeverlan/agentic-wiki`](https://github.com/codeverlan/agentic-wiki).
The local remote uses the SSH alias `github-personal`:

```bash
git remote add origin github-personal:codeverlan/agentic-wiki.git
```

The Codex skill for planning project-specific integrations is installed outside
the repo at:

```text
/Users/tyler-lcsw/.codex/skills/memwiki-planner
```

The project keeps a symlink at `skills/memwiki-planner` pointing to that
installed skill. Skill-link changes are tracked on branch
`skill/memwiki-planner`.

## License

Apache-2.0.
