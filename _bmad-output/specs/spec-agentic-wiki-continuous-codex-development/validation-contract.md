# Validation Contract

## Always Required Before Completion

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

Report pass/fail status and any important warnings.

## Conditional Validation

| Change type | Required validation |
| --- | --- |
| Commands, schemas, workflows, storage layout, validation behavior, or docs generator | `uv run agentic-wiki docs check` |
| Legacy CLI behavior | `uv run memwiki docs check` |
| Capability manifest or mutation policy | Tests covering `src/memwiki/capabilities.py` and generated `.memwiki/agent-capabilities.json` expectations |
| Clinical PHI behavior | HIPAA/local tests plus synthetic-only fixture review |
| Agent-development incident log or PHI-monitoring behavior | Focused renderer/API/CLI tests plus confirmation that incident artifacts omit PHI, credentials, tokens, passwords, and raw secret values |
| Accepted decision matrix or accelerated-interview decision source changes | JSON parse, HTML/JSON-LD parse, control-index link check, memlog entry, and confirmation that plugin files were not mutated |
| Static export | Export tests covering standard and clinical blocked/deidentified paths |
| Public API | API tests for `AgenticWikiWorkspace` and compatibility tests for `MemwikiWorkspace` |

## Evidence Standard

Closeout must name:

- Files changed.
- Plugin workflow skill selected and whether shared plugin assets or project-local outputs were touched.
- Skill mutation permission applied, planned writes checked, and any reroute or blocker caused by a permission mismatch.
- Plugin run mode selected or safely inferred, run-mode requirements checked, and any enabling question or blocker caused by mode ambiguity.
- Run-mode transition classification, transition authority, and any broadening transition blocked for missing authority.
- Authority ledger JSON and HTML paths, append-only policy check, active directives checked, inferred permissions relied on, mode transitions recorded, continuous-run directives checked, expiration or supersession entries appended, and evidence used for broadening-transition authority.
- Worker report JSON and HTML paths for any worker-produced slice, assigned paths, worker lease and heartbeat state, changes made, validation run, blockers, proposed shared-control-artifact changes, authority or resource evidence, and coordinator disposition.
- Control index path, categorized artifact groups, and confirmation that it is navigation-only.
- Handoff digest path, trigger, active authority, queue status, worker lease summary, blocker and validation summary, external-capability artifact links, supervision items, and exact resume command or path when a digest trigger occurred.
- Incident/exception log JSON and HTML paths, PHI-handling answer, HIPAA-aware variant status when applicable, monitoring phases, incident count, severity counts, abnormal events recorded, and confirmation that no PHI, credentials, tokens, passwords, or raw secret values were stored.
- Accepted decision matrix JSON and HTML paths, row count, accepted or superseded status, source confirmation, control-index/dashboard linkage, and confirmation that reusable plugin defaults were not changed.
- Dashboard data contract paths, whether a rich dashboard was requested or generated, external visualization capability used, bounded snapshot source, four-lane status summaries, visible external connection list status, and generated dashboard artifact path or reason none exists.
- Dashboard status-lane evidence when dashboard/status data is prepared: internal agent work, external capability activity, validation/evidence, and supervision-required lanes each include current, blocked, completed, and last-updated summaries while the top-level project status remains combined.
- External-capability registry paths, external-capability result packet paths or worker-report evidence, capabilities used or unavailable, authority source, autonomy boundary, supervision or credential-source boundary, spawned agents, external APIs/MCP servers/plugins/skills involved, and evidence capture.
- External capability result-packet content when used: invocation ID, capability ID, connection type/name/reference, checked time, bounded input/output summaries, artifacts produced, provenance, replay notes, limitations, lifecycle state, supersession links, dashboard visibility, and confirmation that secrets, credential values, hidden reasoning, PHI, and oversized payloads were omitted.
- Result-packet immutability check: accepted packets were not edited in place; corrections, stale results, or reruns created superseding packets with prior/current links, and dashboard data points to the current packet while preserving history links.
- Project-local credential-source metadata when external capability auth is needed: source type, local path or process-env reference, owning capability, auth scheme label, rotation note, and confirmation that secret values were not written to durable artifacts.
- Intent packet JSON and HTML paths, source, project archetype, design pathway status, and preflight readiness result.
- Storage adapter selected, project memory root, primitive path mapping, and whether `_bmad` was detected.
- Intake depth selected, whether standard preflight was required for unattended work, secondary-profile sufficiency, and any deep-spec escalation reason.
- Archetype registry JSON and HTML paths, primary orchestration profile, secondary scoped profiles, mixed-project policy disposition, project-local overrides, and the source for any override.
- Dirty-worktree intake classification before dispatch or closeout, including any user/unowned paths preserved and any overlapping slices stopped.
- Suggested milestone check-ins and whether unattended mode made them advisory.
- Tests added or changed.
- Validation commands run.
- Commands not run and why.
- Non-local source evidence used to justify implementation, blockers, or architecture/product decisions, recorded as metadata rather than copied source content.
- High-risk review result for slices touching PHI/privacy, credentials, public API compatibility, schema/storage layout, plugin-core behavior, generated docs contracts, security boundaries, or deployment/publishing behavior.
- Remaining blockers or open questions.
- Blocked slices, dependent slices paused, and independent slices continued.
- HTML run-state artifact with a per-slice status table when a resume artifact is required.
- HTML handoff digest generated at every stop, compaction-risk point, stale lease, or user-return checkpoint.
- Whether docs or capability manifest updates were required.

## Validation Failure Handling

- A validation failure creates a highest-priority repair task for the failing slice.
- The failing slice gets at most two bounded self-repair attempts before it is stopped as blocked or failed.
- Each repair attempt must record the failing command, failure summary, fix hypothesis, touched paths, and validation rerun.
- After the second unsuccessful repair attempt, stop the failing slice, preserve evidence, preserve or revert only slice-owned changes as appropriate, and continue independent slices.
- Exhausted repair loops must be recorded in the project-local metadata-only incident/exception log; PHI-aware projects must keep that entry free of PHI and secret values.
- Revert slice-owned changes only when they destabilize the workspace, block independent slices, or are clearly not useful to preserve for diagnosis.
- Never roll back unrelated user changes, unrelated dirty-tree changes, or changes owned by other slices.
- The failure blocks only the failing slice and slices that depend on it.
- Independent slices continue when their file ownership, tests, and acceptance criteria do not depend on the failure.
- The coordinator records the failing command, failure summary, affected paths, dependent slices, and independent slice selected next.
- The user-facing closeout or run-state artifact must report every blocked or failing slice when the user returns.
- The run-state artifact must encode each slice as `active`, `blocked`, `waiting-on-dependent`, `validated`, `merged`, or `superseded`.
- The run-state artifact must show worker leases when present, including heartbeat, stale status, stale handling, and reassignment or supersession notes.
- The handoff digest must show objective, trigger, active authority, queue state, worker leases, blockers, validation, external-capability artifacts, supervision items, and exact resume command or path.
- The incident/exception log must show metadata-only abnormal-event evidence, PHI-aware monitoring phases when applicable, and no stored PHI, credentials, tokens, passwords, or raw secret values.
- `slice-queue.json` is the source of truth for slice state; HTML run-state tables are human-readable renderings and must not diverge from it.

## High-Risk Review Gate

- Require review-worker or adversarial review before merge or integration for slices touching PHI/privacy, credentials, public API compatibility, schema/storage layout, plugin-core behavior, generated docs contracts, security boundaries, or deployment/publishing behavior.
- The review must record reviewed paths, risk surface, findings, required fixes, and final disposition.
- Ordinary low-risk implementation slices may rely on focused tests, required validation, and coordinator review.
- If review capacity is unavailable, stop only the affected high-risk slice and continue independent slices unless the review gap affects all remaining work.

## Docs Governance

When docs are affected, use the project workflow rather than hand-authoring generated output as final truth. In a source checkout, docs verification may use a disposable initialized workspace and compare generated docs with tracked `docs/*.html`.
