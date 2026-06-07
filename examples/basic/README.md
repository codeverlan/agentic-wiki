# Basic Agentic Wiki Example

This directory contains small text and HTML sources plus generated PDF/image
fixtures. Regenerate binary fixtures with:

```bash
uv run python examples/basic/generate_fixtures.py
```

Then ingest files into a workspace:

```bash
uv run agentic-wiki --workspace /tmp/agentic-wiki-basic init
uv run agentic-wiki --workspace /tmp/agentic-wiki-basic ingest examples/basic/source.txt
uv run agentic-wiki --workspace /tmp/agentic-wiki-basic ingest examples/basic/page.html
uv run agentic-wiki --workspace /tmp/agentic-wiki-basic ingest examples/basic/sample.pdf
uv run agentic-wiki --workspace /tmp/agentic-wiki-basic ingest examples/basic/image.png
uv run agentic-wiki --workspace /tmp/agentic-wiki-basic query "semantic HTML" --json
uv run agentic-wiki --workspace /tmp/agentic-wiki-basic capabilities
```
