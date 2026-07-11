# Task Graph

## Priority Order

1. Safety and contract preservation: PHI policy, source immutability, draft-before-promote, JSON-LD HTML, provenance.
2. Public surface stability: `agentic_wiki` API, `memwiki` shim, CLI JSON outputs, capability manifest.
3. Behavior changes with tests: bug fixes, workflow changes, validation changes.
4. Documentation and generated artifact alignment.
5. Refactors that reduce risk without changing behavior.

## Authoritative Work Source

1. Use BMAD artifacts first: sprint plans, stories, spec files, task docs, and implementation artifacts under `_bmad-output/`.
2. Use agent-derived safe fallback tasks only when BMAD artifacts are empty, blocked, or silent on the next safe action.
3. When deriving fallback work, choose from the fallback queue in this file and record why BMAD artifacts could not drive the next action.
4. Do not let generated fallback work override BMAD decisions, user instructions, repo-local `AGENTS.md`, or the spec bundle.

## Task Shape

Each implementation task must declare:

- Objective.
- User-visible or agent-visible behavior.
- In-scope file group.
- Out-of-scope file groups.
- First failing test or characterization test.
- Expected validation commands.
- PHI-handling and incident/exception log impact.
- Documentation or manifest impact.
- Fallback task if blocked.
- Independent parallel slice candidates.
- Dependency links to other active or queued slices.
- Proposed-by field when a worker discovered the slice.
- Machine-readable queue entry in `_bmad-output/implementation-artifacts/slice-queue.json`.

## Dependency Rules

- Do not implement behavior before a failing or characterization test exists unless the task is pure documentation or pure scaffolding.
- Do not update docs as final truth until behavior and tests pass.
- Do not change CLI output shape without updating API/CLI tests and docs.
- Do not change clinical behavior without policy tests and synthetic-only fixtures.
- Do not modify generated docs manually when the docs generator should own the output.
- Keep the task graph wide enough to identify independent parallel slices whenever possible.
- A blocker or failed validation in one slice blocks only that slice and dependent slices, not unrelated project work.
- When a slice blocks, switch to the highest-priority independent slice that does not depend on the blocker.
- Workers propose newly discovered slices to the coordinator rather than creating or assigning them directly.
- The coordinator may autonomously create and assign new safe slices without user approval when the slice stays inside the current BMAD/spec boundaries and no stop condition is crossed.
- The coordinator keeps `_bmad-output/implementation-artifacts/slice-queue.json` aligned with the active task graph.

## Fallback Queue

Use this queue when BMAD artifacts are empty, blocked, or silent on the next safe action, and the fallback does not require the blocked decision:

1. Move to the highest-priority independent parallel slice already identified.
2. Split a broad task into smaller story-ready slices and identify at least one independent slice.
3. Create or repair the project-local intent packet when the plugin cannot form a clear work picture from guided interview answers, existing resources, or hybrid intake.
4. Select or repair the project-local storage adapter and primitive path mapping.
5. Select or repair the plugin workflow skill routing and mutation-permission fit: intake/preflight, continuous-run coordinator, status/report handoff, or plugin-improvement review.
6. Select or repair plugin run-mode routing and requirements: interactive-intake, unattended-continuous-run, status-only-report, or plugin-improvement-review.
7. Select or repair run-mode transition classification and broadening authority.
8. Create or repair the project-local authority ledger JSON and semantic HTML forms, then record active directives, inferred permissions, mode transitions, continuous-run directives, expiration or supersession entries, and broadening authority evidence without rewriting prior ledger source entries.
9. Create or repair the project-local worker-report JSON and semantic HTML templates so workers can submit assigned paths, lease/heartbeat state, changes made, validation run, blockers, proposed shared-control-artifact changes, and authority or resource evidence before coordinator acceptance.
10. Create or repair the project-local control index so users and future agents can navigate current artifacts without treating the index as a source of truth.
11. Create or repair the handoff digest template and latest digest renderer output so stops, compaction risk, stale leases, and user-return checkpoints have a resume surface.
12. Create or repair the incident/exception log JSON and semantic HTML surfaces, including the HIPAA-aware metadata-only variant and monitoring phases when the application handles PHI.
13. Create or repair the dashboard data contract templates that external visualization capabilities can consume when a rich dashboard is requested.
14. Create or repair the external-capability registry for project-scoped plugins, skills, APIs, MCP servers, browser tools, external services, and spawned agents.
15. Create or repair synchronized intent-packet JSON and semantic HTML forms.
16. Select or repair the intake depth; elevate quick start to standard preflight before unattended continuous development, and escalate to deep spec for high-risk or unclear work.
17. Create or repair the project-local archetype registry JSON and semantic HTML review surface.
18. Classify project archetype and replace generic intake with domain-specific questions when the project type is clear.
19. For mixed projects, select or repair the primary orchestration profile and secondary scoped profiles before deriving slices.
20. Capture design pathway agreement before visual/frontend implementation slices depend on graphic direction.
21. Repair a failed preflight readiness gate with an enabling slice when that does not cross a stop condition.
22. Implement or validate the run-state, handoff digest, and incident-log HTML renderers before the first real continuous run if that preflight requirement is still missing.
23. Add or tighten characterization tests for current behavior.
24. Run focused docs drift checks and report exact drift.
25. Update stale generated docs after behavior is already proven.
26. Improve capability manifest tests where behavior is already defined.
27. Review existing diff for PHI, provenance, source immutability, JSON output, and compatibility regressions.
28. Prepare a no-code implementation plan with exact files, tests, blockers, and independent slice candidates.

## Stop Conditions

Stop and ask the user before:

- Removing `memwiki` compatibility surfaces.
- Using computer-use, GUI-driving, or human-desktop-control workflows without user supervision.
- Changing git remotes, repository ownership, package publishing targets, or release credentials.
- Using real PHI or importing PHI-bearing material.
- Adding remote model adapters, telemetry, cloud PHI storage, or static PHI export for clinical workspaces.
- Overwriting user-authored docs, BMAD artifacts, or generated workspace output.

## Continuous Delivery Default

Future Codex sessions may create branches, edit files, run commands, stage changes, commit, push, open PRs, and continue approved fallback tasks without asking again, provided the active user request does not narrow scope and no stop condition is crossed.

## PR Workflow

1. Treat PRs as audit, synchronization, and visibility artifacts, not approval gates.
2. Do not stop continuous development merely because a PR was opened or awaits review.
3. If validation passes and repo policy allows agent-managed merge, Codex may merge and continue without waiting for user approval.
4. If branch protection, platform policy, or missing credentials blocks merge, record that external merge blocker and continue safe work on the active branch or a follow-on branch.
5. Never use PR review as a substitute for tests, docs checks, PHI boundary review, or BMAD acceptance criteria.

## Continuous Run Stop Order

1. Stop at an explicit user-specified time, token, work, or budget boundary.
2. Treat platform or host usage limits as hard stops; if a run is likely to hit a known limit, close cleanly before losing state.
3. If no explicit boundary is set, continue until the BMAD queue and safe fallback queue are empty or all remaining work is blocked by a stop condition.
4. Before stopping for queue drain, run the associated tests and validation for completed work.
5. If tests fail at the queue-drain boundary, continue or create a focused repair task unless a time/budget or stop-condition boundary prevents it.
6. When a limit or stop condition interrupts before queue drain, write a durable resume artifact under `_bmad-output/implementation-artifacts/`.
7. When the interruption involved abnormal behavior, repair-loop exhaustion, expected resource outage, suspicious access, credential/secret exposure risk, or PHI monitoring concern, append metadata-only evidence to the incident/exception log and render the HTML review surface.

## Blocked Slice Reporting

When any slice blocks, preserve a report with:

- Slice ID and branch.
- Blocker type.
- Evidence gathered.
- Files touched.
- Dependent slices paused.
- Independent slice selected next.
- Validation already run and still required.
- User decision needed, if any.
