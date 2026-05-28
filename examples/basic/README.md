# Basic Memwiki Example

This directory contains small text and HTML sources plus generated PDF/image
fixtures. Regenerate binary fixtures with:

```bash
uv run python examples/basic/generate_fixtures.py
```

Then ingest files into a workspace:

```bash
uv run memwiki --workspace /tmp/memwiki-basic init
uv run memwiki --workspace /tmp/memwiki-basic ingest examples/basic/source.txt
uv run memwiki --workspace /tmp/memwiki-basic ingest examples/basic/page.html
uv run memwiki --workspace /tmp/memwiki-basic ingest examples/basic/sample.pdf
uv run memwiki --workspace /tmp/memwiki-basic ingest examples/basic/image.png
uv run memwiki --workspace /tmp/memwiki-basic query "semantic HTML" --json
uv run memwiki --workspace /tmp/memwiki-basic capabilities
```
