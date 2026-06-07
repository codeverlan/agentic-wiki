# Synthetic PHI Fixtures

This directory contains synthetic outpatient mental health client records for
local development and workflow testing only.

Generate or regenerate the per-client JSON fixtures with:

```bash
uv run python fixtures/synthetic_phi/generate_records.py
```

The generator writes one JSON file per synthetic client record under
`fixtures/synthetic_phi/records/`.
