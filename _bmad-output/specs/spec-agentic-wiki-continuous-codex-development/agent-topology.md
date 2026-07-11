# Agent Topology

## Purpose

Define the safe parallel shape for Codex work on Agentic Wiki. This file controls role boundaries, concurrency, ownership, and merge expectations for multi-agent runs.

## Roles

| Role | Owns | Must not own |
| --- | --- | --- |
| Coordinator | Intake, intent-packet normalization, PHI-handling question capture, project-archetype classification, design-pathway scoping, preflight readiness, repo status, dirty-worktree classification, autonomous task slicing, file ownership, slice assignment, shared control artifacts, incident-log mutation, final integration, validation evidence | Large implementation edits while workers are active |
| Test worker | Focused failing tests, fixtures, regression assertions, docs-check expectations | Production fixes outside the assigned slice |
| Implementation worker | Small code changes for one approved slice | Shared architecture changes without coordinator approval |
| Docs worker | Generated docs, CLI reference, schema/workflow docs, capability manifest explanation | Behavior changes without tests |
| Review worker | Diff review, acceptance criteria check, PHI/local-only boundary review | Rewriting the implementation under review |
| External capability worker | Scoped work delegated through a plugin, skill, API, MCP server, browser tool, external service, or spawned agent | Shared control artifact mutation or unsupervised actions outside the authority/autonomy/supervision boundary |

## Default Concurrency

- Use one coordinator.
- Use up to six simultaneous workers by default.
- Never assign two workers to the same file group at the same time.
- Prefer parallel reads and analysis before parallel writes.
- Use narrower workers for risky areas: clinical PHI policy, source immutability, promotion, export, and public API compatibility.

## File Ownership Groups

| Group | Typical files | Notes |
| --- | --- | --- |
| Public API | `src/agentic_wiki/**`, `src/memwiki/api.py`, `src/memwiki/workspace.py` | Preserve `AgenticWikiWorkspace` as canonical and `MemwikiWorkspace` as shim. |
| CLI | `src/memwiki/cli.py`, command tests, docs command lists | CLI remains a thin wrapper around public API. |
| Memory workflow | `compiler.py`, `promote.py`, `linter.py`, `manifest.py`, `graph.py`, `html.py` | Preserve drafts before canonical wiki output and JSON-LD HTML. |
| Source ingest | `sources.py`, extractors, fixtures | Raw source immutability and provenance are mandatory. |
| Clinical policy | `policy.py`, `exporter.py`, HIPAA tests/docs | No actual PHI; require `OperationContext` where applicable. |
| Agent capabilities | `capabilities.py`, generated `.memwiki/agent-capabilities.json`, capability tests | Canonical and legacy entries must stay aligned. |
| Docs | `docs/*.html`, `src/memwiki/docs.py`, docs tests | Update generated docs when commands, schemas, workflows, storage, or validation changes. |

## Coordination Rules

- Each worker receives: task ID, file group, allowed paths, forbidden paths, expected failing test, expected passing validation, fallback if blocked, and a coordinator-owned lease with heartbeat expectations.
- Before dispatching workers, the coordinator confirms the intent packet and preflight readiness gate are complete or creates an enabling/blocker slice.
- The coordinator adapts worker prompts and allowed paths to project archetype and records design-pathway dependencies before visual/frontend implementation slices begin.
- Before dispatching workers, the coordinator classifies existing dirty paths as active slice, prior-agent work, user/unowned work, or generated artifact.
- Workers must not touch user/unowned dirty paths or unclear dirty overlaps; if a worker discovers one, it stops that slice and reports the overlap.
- Workers report only evidence and diffs, not broad rewrites.
- Only the coordinator may mutate shared control artifacts: `_bmad-output/implementation-artifacts/slice-queue.json`, branch ledger, run-state artifacts, handoff digest artifacts, incident-log artifacts, supervision queue, future-resource queue, plugin-improvement queue, project overlay, and similar coordination files.
- Workers may modify only assigned code, docs, tests, fixtures, their own slice-local worker report, or approved slice-local artifacts.
- Workers must not directly edit shared control artifacts; they submit proposed queue, ledger, run-state, handoff digest, incident-log, supervision, resource, plugin-improvement, or overlay changes in a worker report.
- The coordinator accepts or rejects proposed control-artifact changes after reviewing worker evidence and validation.
- Coordinator integrates serially and reruns validation after integration.
- High-risk slices require review-worker or adversarial review before merge or integration.
- High-risk surfaces include PHI/privacy, credentials, public API compatibility, schema/storage layout, plugin-core behavior, generated docs contracts, security boundaries, and deployment/publishing behavior.
- Ordinary low-risk implementation slices may proceed with tests plus coordinator review.
- If a worker discovers cross-group impact, it stops and reports the dependency rather than expanding scope.
- The coordinator keeps at least two ready independent slices identified whenever the BMAD queue has enough work: the active slice and the next independent slice.
- If slice A blocks, the coordinator records the blocker, marks dependent slices as waiting, and moves workers to independent slice B rather than stopping the run.
- Blocked slice reports must be preserved for the user and the next resume artifact, including blocker cause, affected dependencies, evidence gathered, and the slice chosen next.
- Workers may propose new slices when they discover separable work.
- Only the coordinator creates and assigns slices so file ownership and dependency tracking stay coherent.
- The coordinator is autonomous and does not need user approval to create or assign new safe slices within the existing BMAD/spec boundaries.
- If a worker lease becomes stale, the coordinator marks that lease stale, inspects and preserves slice-owned work, and then reassigns or supersedes only that slice while independent slices continue.
- When a worker encounters abnormal behavior, expected-but-unavailable resources, repair-loop exhaustion, suspicious access patterns, credential/secret exposure risk, or PHI-monitoring concerns, the worker records a proposed metadata-only incident entry in its report. The coordinator decides whether to append the entry to the shared incident log.

## External Capability Coordination

The coordinator may use external capabilities when a slice needs functions beyond the reusable plugin's built-in surfaces. External capabilities include installed plugins, skills, APIs, MCP servers, browser tools, external services, visualization tools, and spawned agents. Use is autonomous only when it stays inside active project authority, development/local boundaries, and existing supervision rules.

Before assigning external capability work, the coordinator records the capability in `_bmad-output/implementation-artifacts/external-capabilities/` with purpose, related slice, availability, authority source, autonomy boundary, supervision or credential-source boundary, expected input, expected output, fallback, evidence capture, and result-packet path when invoked. If auth is needed, the coordinator may reference a project-local env or JSON source, process environment, or approved ephemeral credential; it must not record secret values or force a standardized credential payload.

External capability workers receive scoped prompts or requests with allowed paths, forbidden paths, sensitive-action boundaries, validation expectations, and required worker-report or result-packet output. They return evidence through a slice-local worker report or `_bmad-output/implementation-artifacts/external-capability-results/` JSON/HTML packet before the coordinator accepts, rejects, repairs, or supersedes the work. After coordinator acceptance, a result packet is immutable; workers or the coordinator create a superseding result packet for corrections or reruns instead of editing the accepted packet.

External capability use follows resource-gap handling: expected-but-unavailable capabilities stop only the affected slice with chat notification, while useful nonexistent capabilities become future-resource opportunities.

## Worker Report Contract

Every worker closeout must produce synchronized JSON and semantic HTML forms under `_bmad-output/implementation-artifacts/worker-reports/`. The coordinator must review the worker report before accepting, rejecting, merging, or superseding the worker output.

Every worker report must include:

- Task ID and assigned file group.
- Worker ID and slice ID.
- Assigned paths and forbidden paths.
- Files changed and files intentionally not touched.
- Summary of changes made.
- Tests or validation run.
- Evidence gathered.
- Proposed metadata-only incident entry when the worker encountered abnormal behavior, repair-loop exhaustion, suspicious behavior, expected resource outage, credential/secret exposure risk, or PHI-monitoring concern.
- Blockers or dependency discoveries.
- Dirty-path overlaps discovered or avoided.
- Proposed control-artifact changes, if any.
- Authority ledger entries, resource evidence, and non-local source evidence used, if any.
- Slice-local decisions made and rationale.
- High-risk review finding or reason the slice is low-risk.
- Coordinator disposition: pending, accepted, rejected, needs repair, or superseded.

Proposed shared-control-artifact changes inside a worker report are evidence only. The coordinator applies accepted changes after reviewing validation, ownership, and risk.
