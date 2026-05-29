# Agentic Wiki

Agentic Wiki is an HTML-first memory system for turning raw information into a living, source-backed knowledge site.

It starts from a simple idea: if an AI system is going to remember things over time, that memory should be readable by people, inspectable by agents, portable across tools, and built from sources that can be checked later.

This project is inspired by Andrej Karpathy's original [LLM Wiki concept](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), which described a persistent wiki compiled from raw materials and updated over time by agents. Agentic Wiki keeps that core pattern and extends it into a more complete open-source system: semantic HTML as the canonical format, claim-level provenance, draft review before promotion, local-first storage, static publishing, and agent-friendly integration surfaces.

## What It Does

Agentic Wiki turns scattered information into a structured knowledge base that can be read as a website, queried by an agent, reviewed by a human, and exported as static HTML.

It is designed for projects where memory should not be a black box.

- Raw files stay preserved.
- Generated knowledge starts as a draft.
- Accepted pages become semantic HTML.
- Claims point back to their sources.
- Links and manifests create a durable knowledge graph.
- Documentation can be checked and updated as the system evolves.
- Remote AI providers are opt-in, not assumed.

## Why HTML

Most memory-wiki experiments start with Markdown. Markdown is useful, but HTML gives this project a stronger long-term foundation.

HTML is already the language of the web. It can carry readable content, structured metadata, embedded JSON-LD, source citations, figures, tables, links, and semantic sections in one portable format. A human can open it. A browser can render it. A search engine can crawl it. An agent can parse it.

That makes Agentic Wiki useful as more than a private memory store. It can become documentation, a research wiki, a static website, a source-backed content system, or the memory layer inside another application.

## What Makes This Different

Agentic Wiki builds on the original wiki-memory idea with a few deliberate upgrades:

- **HTML-first memory:** canonical pages are semantic HTML, not opaque embeddings or hidden database rows.
- **Source-backed claims:** important claims can carry source IDs, locators, confidence, review status, and timestamps.
- **Draft-before-publish workflow:** generated pages land in drafts before they are promoted into the accepted wiki.
- **Plain-file storage:** sources, pages, manifests, links, claims, and events are stored in ordinary files.
- **Agent-ready operations:** dry-run ingest, check-only promotion, JSON query output, object resolution, backlinks, and capabilities are exposed for coding agents.
- **Static export:** the wiki can become a browsable static site.
- **Self-evolving documentation:** docs are treated as artifacts that can be checked, drafted, and promoted like content.
- **Open-source posture:** the project is designed to be inspectable, portable, and provider-agnostic.

## Use Cases

Agentic Wiki is meant for builders who want memory to compound across sessions, projects, and tools.

Good fits include:

- A personal or team knowledge base that stays grounded in original sources.
- An agent-accessible project memory that survives beyond a single chat thread.
- A documentation system that can ingest PDFs, notes, HTML, Markdown, and images.
- A research wiki where claims remain traceable to source material.
- A static website generated from curated knowledge.
- An SEO content system where public pages remain readable, crawlable, and source-backed.
- A plugin, coding-agent, or application feature that needs durable memory without adopting a database-first CMS.

## Cloudflare and SEO Direction

One active concept branch explores Agentic Wiki as a supplement for readable website content and SEO optimization on Cloudflare.

The idea is to use the wiki as the source-backed content memory behind a static site:

- Option 1: publish reviewed HTML through a Git-based static workflow.
- Option 2: allow authenticated API updates with Cloudflare Workers and R2-backed live content.
- Hybrid: keep high-traffic pages static while using Workers and R2 for selected dynamic publishing workflows.

This direction is especially interesting for sites that need fresh content, internal linking, structured data, provenance, and static-page performance without turning the content layer into a closed CMS.

See the `concept/cloudflare-seo` branch for the standalone HTML concept documents.

## Project Branches

The repository currently has a few useful branches:

- `main` is the baseline implementation branch.
- `concept/cloudflare-seo` contains the Cloudflare SEO content-memory concept and implementation-plan documents.
- `skill/agentic-wiki-planner` contains the Codex skill symlink and documentation for the Agentic Wiki integration planner.

The planner skill is intended to be invoked while working in another codebase. It analyzes that project first, then recommends how Agentic Wiki could fit, including Cloudflare implementation options when relevant.

## Current Shape

The project currently ships as a Python package and CLI named `memwiki` while the repository and product direction move under the Agentic Wiki name.

Today it can:

- initialize a local wiki workspace
- ingest text, Markdown, HTML, PDF, and image files
- extract source text and metadata
- compile draft HTML pages
- promote validated drafts into the accepted wiki
- query wiki content with citations
- inspect pages, sources, claims, and backlinks
- export a static HTML site
- check and draft documentation updates

The important part is not the command names. The important part is the workflow: preserve sources, generate drafts, validate them, promote accepted knowledge, and keep the result readable by both people and agents.

## Try It Locally

For now, this is a developer-facing project. The fastest way to explore it is with `uv`:

```bash
uv sync
uv run memwiki --help
```

A minimal flow looks like this:

```bash
uv run memwiki --workspace my-wiki init
uv run memwiki --workspace my-wiki ingest path/to/source.html
uv run memwiki --workspace my-wiki compile <source-id>
uv run memwiki --workspace my-wiki promote <draft-id>
uv run memwiki --workspace my-wiki export static --output my-site
```

The Python API exposes the same workflow through `MemwikiWorkspace`, which is the intended integration point for other programs and coding agents.

## Credits

Agentic Wiki is directly inspired by Andrej Karpathy's [LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). That concept made the case for a persistent, compounding wiki built from raw sources and maintained by agents.

This repository carries that idea forward with an HTML-first format, open-source implementation, source-level provenance, reviewable draft promotion, static publishing, documentation governance, and integration patterns for modern agent workflows.

## License

Apache-2.0.
