# Handoff Runbook

## Start Here

1. Read `SPEC.md`.
2. Read every file in `companions:`.
3. Read repo-local `AGENTS.md` again if the worktree or branch changed.
4. Read `plugin-project-boundary.md` before applying project-specific guidance from any plugin, skill, or BMAD artifact.
5. Resolve conflicting guidance in this order: system/developer/user safety and active request; repo-local `AGENTS.md` and explicit user project instructions; BMAD artifacts when present; project-local plugin overlay memory; plugin defaults; agent judgment.
6. Normalize user intent into a project-local intent packet from quick start, standard preflight, deep spec, existing spec/resource referral, or hybrid resource referral plus clarifying questions.
7. Select the project-local storage adapter: BMAD adapter when `_bmad` exists, lightweight Agentic Wiki adapter otherwise; verify the adapter primitive paths are writable.
8. Select the current plugin workflow skill: intake/preflight, continuous-run coordinator, status/report handoff, or plugin-improvement review.
9. Verify the selected skill's mutation permission covers the planned writes; reroute or stop the affected slice when it does not.
10. Select or safely infer the plugin run mode: interactive-intake, unattended-continuous-run, status-only-report, or plugin-improvement-review.
11. Verify run-mode requirements: unattended continuous run requires standard preflight for the primary profile; status-only report remains read/report-only except generated reports; plugin-improvement review must not mutate plugin files unless explicit plugin-development work has started.
12. If the run mode changes, classify the transition as narrowing or broadening; narrowing transitions may proceed autonomously, while broadening transitions require the authority recorded in `plugin-project-boundary.md` and checked against the project-local authority ledger.
13. Select intake depth. Use standard preflight as the minimum for unattended continuous development on the primary profile; use essential fields for secondary profiles unless they become high-risk or shape shared architecture.
14. Maintain both intent-packet forms: JSON as dispatch input and semantic HTML as the human-facing review surface.
15. Load the archetype registry, apply any project-local registry overrides, classify project archetype, and select the appropriate interview path; for mixed projects, choose one primary orchestration profile and attach secondary scoped profiles instead of forcing all work through one generic path.
16. For visual or frontend work, capture graphic-design agreement and use image generation, installed design/UX skills, or HTML mockups as user-selectable pathway aids when useful.
17. Translate the intent packet into needed resources, skill sets, validation depth, PHI-handling posture, incident-log monitoring needs, escalation sources, supervision-required actions, and suggested milestone check-ins; do not frame this as sandbox configuration.

Before reopening any interview question or dispatching implementation from inferred requirements, load `_bmad-output/implementation-artifacts/accepted-decisions/decision-matrix.json` and its semantic HTML review surface. Treat accepted rows as project-local source-of-truth decisions unless a later project-local correction or supersession exists.

18. Run `git status --short --branch`.
19. Classify every dirty path as active slice, prior-agent work, user/unowned work, or generated artifact before dispatching workers.
20. Stop only planned slices that overlap unclear dirty paths; preserve user/unowned work and continue independent slices when available.
21. Identify whether the request is implementation, review, docs, investigation, or planning.
22. Load BMAD work first when `_bmad` exists: sprint plans, stories, spec files, task docs, and implementation artifacts under `_bmad-output/`.
23. If `_bmad` is absent in a future project, use the plugin's lightweight project-local agent-development memory instead of requiring BMAD.
24. Load `_bmad-output/implementation-artifacts/slice-queue.json` if it exists; create it from `delivery-workflow.md` if missing.
25. Run the preflight readiness gate: spec/handoff loaded, intent-packet JSON and HTML forms present, explicit PHI-handling question answered or queued, authority ledger JSON and HTML forms loaded or generated, accepted decision matrix JSON and HTML forms loaded or generated, worker-report JSON and HTML templates loaded or generated, control index loaded or generated, handoff digest template loaded or generated, incident/exception log JSON and HTML surfaces loaded or generated, dashboard data and external-capability templates loaded or generated when needed, plugin workflow skill selected, skill mutation permission matched to planned writes, plugin run mode selected or safely inferred, run-mode requirements satisfied, mode transition authority verified against the authority ledger when a transition occurs, storage adapter selected and writable, intake depth selected and sufficient for requested work mode, archetype registry loaded and applied with primary and secondary profiles recorded when relevant, queue parseable, dirty-worktree intake complete, branch ledger current, renderer available, validation baseline selected, resource/credential-source/supervision needs identified, PHI-aware monitoring phases present when the application handles PHI, project archetype classified, design pathway status recorded when relevant, and at least one safe slice or enabling slice ready.
26. If the preflight gate fails, create an enabling or blocker slice; stop the whole run only when no independent work exists or a stop, safety, or supervision boundary applies.
27. Before the first real continuous run, verify tested renderers exist for deriving run-state and handoff digest HTML from `slice-queue.json` and incident-log HTML from incident-log JSON; if missing, make that the first enabling slice.
28. Select a task shape from `task-graph.md`.
29. Identify at least two independent parallel slices whenever enough work exists.
30. Let workers propose separable work, but have only the coordinator create and assign new slices.
31. Create safe new slices autonomously when they stay inside the current BMAD/spec boundaries and no stop condition is crossed; do not wait for user approval.
32. Keep `_bmad-output/implementation-artifacts/slice-queue.json` aligned with created, assigned, blocked, validated, merged, and superseded slices.
33. Keep shared control artifacts single-writer: only the coordinator mutates slice queue, branch ledger, run-state artifacts, handoff digest artifacts, incident-log artifacts, supervision/resource/plugin queues, and project overlays.
34. Workers modify only assigned code, docs, tests, fixtures, their own slice-local worker report, or approved slice-local artifacts; they submit proposed control-artifact changes in worker reports for coordinator acceptance after validation.
35. During unattended runs, put routine progress in local artifacts rather than chat; chat is for expected-but-unavailable resources, exhausted repair loops, supervision-required tasks, whole-run stops, safety/privacy boundaries, user-return queue presentation, and user-requested summaries. Record abnormal coordinator events and PHI-monitoring alerts in the project-local metadata-only incident log.
36. Suggested milestone check-ins remain advisory during unattended continuous development unless a stop, safety, or supervision boundary applies.
37. When the user requests a status summary, generate it from current local artifacts without treating the request as approval to interrupt safe active work unless the user explicitly asks to stop or redirect.
38. Update `_bmad-output/implementation-artifacts/control-index.html` when artifact links, dashboard surfaces, or major control files change.
39. Generate or update `_bmad-output/implementation-artifacts/handoff-digest/` when a run stops, compaction risk appears, a worker lease is stale, or the user returns. Generate or update `_bmad-output/implementation-artifacts/incident-log/` when an abnormal coordinator event, repair-loop exhaustion, expected resource outage, safety/privacy boundary, credential/secret exposure risk, suspicious behavior, or PHI monitoring alert occurs.
40. When rich dashboarding is requested, prepare bounded dashboard data under `_bmad-output/implementation-artifacts/dashboard-data/` and coordinate with an external visualization capability such as `build-web-data-visualization` or Data Analytics widgets rather than hard-coding dashboard models into the plugin.
41. When external resources are needed, record and coordinate them through `_bmad-output/implementation-artifacts/external-capabilities/`, including plugins, skills, APIs, MCP servers, browser tools, external services, and spawned agents.
42. External capabilities may be used autonomously only inside active project authority, development/local boundaries, and existing supervision rules; stop or queue supervision for credentials, production changes, publishing, personal-account actions, broad computer-use, sensitive data access, or unclear authorization.
43. If the user chooses to pass a credential in chat for development work, use it only for the approved task and make best efforts not to store, echo, log, commit, or persist the value in durable artifacts.
44. When the project needs reusable external capability auth, use a project-local env file, JSON file, process environment, or approved ephemeral source; record only source metadata, never credential values, and do not standardize provider-specific credential payload shape in plugin code.
45. Assume ordinary credential use is development-only unless the user explicitly says otherwise; production credentials, policies, and passwords are separate and require explicit direction or supervision.
46. If BMAD work is empty, blocked, or silent on the next safe action, choose an agent-derived safe fallback task from `task-graph.md` and record why fallback was used.
47. For implementation, write the first failing or characterization test before production edits.
48. Make reversible implementation-level decisions autonomously when they stay inside current requirements and safety boundaries; record the decision and rationale in run evidence.
49. Use continuous-delivery autonomy by default: branch, edit, test, stage, commit, push, open PRs, and continue fallback work unless the active user request narrows scope or a stop condition is crossed.
50. Use one coordinator plus up to six simultaneous workers by default.
51. If a slice blocks, record the blocked-slice report and move to the highest-priority independent slice rather than stopping the project.
52. If an unresolved architecture or product decision appears, stop only the affected slice and its dependents unless the decision affects the run boundary, shared architecture, safety constraints, plugin-core mutation, PHI/privacy boundary, public API compatibility, or all remaining work.
53. If validation fails after an implementation slice, allow at most two bounded self-repair attempts inside that slice before stopping it and continuing independent slices.
54. Never roll back unrelated user changes, unrelated dirty-tree changes, or changes owned by other slices during validation repair; preserve or revert only slice-owned changes as appropriate.
55. Record any explicit time, token, work, budget, or platform usage boundary before dispatching work.
56. Before declaring ordinary difficulty blocked, follow the mandatory escalation ladder in order: local repo/tests/logs/docs/BMAD/Agentic Wiki memory; package/tool output and official project docs; web search or current official ecosystem docs; installed skills/plugins and scoped browser verification; parallel or adversarial worker review; then slice splitting, independent-slice continuation, or supervision-queue entry.
57. Record non-local source evidence whenever web search, official docs, browser verification, installed plugins, or external tool output justify implementation, blocker, or architecture/product decisions; record metadata only, not large source content.
58. Require review-worker or adversarial review before merge or integration for high-risk slices touching PHI/privacy, credentials, public API compatibility, schema/storage layout, plugin-core behavior, generated docs contracts, security boundaries, or deployment/publishing behavior.
59. Use scoped browser or Chrome-control plugins autonomously only for development verification, rendered-route inspection, docs research, screenshots, accessibility checks, and local app validation.
60. Queue supervision-required tasks in `_bmad-output/implementation-artifacts/supervision-queue.html`; when the user returns, present that queue at the next reasonable stopping point without necessarily interrupting active safe work.
61. Never mutate reusable plugin files, plugin cache files, marketplace entries, or global skill files to encode project-specific guidance unless the user explicitly starts a plugin-development workflow.
62. Apply project-local overlays from `_bmad-output/implementation-artifacts/project-plugin-overlay.html` after plugin defaults when shaping plugin output, guidance, reports, autonomy level, skill use, and behavior for this project, but never over higher-priority instructions.
63. Record reusable plugin improvement candidates in `_bmad-output/implementation-artifacts/plugin-improvement-queue.html` rather than mutating plugin files during project work.
64. Use `codex/<date>-<short-goal>` for user-facing branches and `codex/auto/<date>-<queue-slice>` for unattended queue-slice branches.
65. Update `_bmad-output/implementation-artifacts/branch-ledger.html` when a branch is created, materially updated, PR-linked, merged, blocked, or superseded.
66. Treat PRs as non-blocking audit and synchronization artifacts; do not wait for traditional PR approval before continuing development.
67. Do not use broad computer-use, GUI-driving, human-desktop control, personal-account login, external production changes, sensitive pages, purchases, publishing, messaging, or irreversible account actions without user supervision.

## Standard Coordinator Prompt

Use this when handing a slice to another Codex agent:

```text
You are working on Agentic Wiki. Read the BMAD spec bundle at _bmad-output/specs/spec-agentic-wiki-continuous-codex-development/SPEC.md and its companions before acting.

Task:
- Objective:
- Allowed paths:
- Forbidden paths:
- Required first test:
- Required validation:
- Fallback if blocked:

Preserve Agentic Wiki constraints: semantic HTML with JSON-LD, draft-before-promote, claim provenance, raw source immutability, canonical agentic_wiki API, memwiki compatibility, JSON command outputs, generated docs alignment, and synthetic-only clinical PHI tests.
```

## Safe First Tasks

When no specific implementation request is provided, choose one:

- Inspect current tests and identify the narrowest missing regression around a known contract.
- Check docs drift with `uv run agentic-wiki docs check`.
- Check canonical/legacy capability manifest alignment.
- Review public API and CLI surfaces for behavior not covered by tests.
- Prepare the next story-ready task with objective, paths, tests, and blockers.

## Closeout Format

Close with:

- What changed.
- Validation run.
- Files to review.
- Plugin workflow skill selected, whether shared plugin assets were referenced or instantiated, and confirmation that project outputs went through the selected storage adapter.
- Skill mutation permission applied, planned writes checked, and any reroute or stop caused by a permission mismatch.
- Plugin run mode selected or safely inferred, run-mode requirements checked, and any enabling question or blocker caused by mode ambiguity.
- Run-mode transition classification, transition authority, and any narrowing transition performed autonomously or broadening transition blocked.
- Authority ledger JSON and HTML paths, append-only policy check, active directives used, inferred permissions relied on, mode transitions recorded, continuous-run directives checked, and expiration or supersession entries appended.
- Intent packet JSON and HTML paths, source, and any clarifying questions asked.
- Storage adapter selected, project memory root, primitive path mapping, and whether `_bmad` was detected.
- Intake depth selected, whether standard preflight was required for unattended work, and any deep-spec escalation reason.
- Archetype registry JSON and HTML paths, primary orchestration profile, secondary scoped profiles, mixed-project policy disposition, and project-local overrides applied.
- Project archetype, selected domain-specific interview path, and any skipped generic questions.
- Design pathway status, generated image/mockup/design-skill artifacts considered, and selected/deferred visual direction.
- Preflight readiness gate result, including enabling or blocker slices created.
- Dirty-worktree intake classification, including any user/unowned paths preserved and any overlapping slices stopped.
- Suggested milestone check-ins and whether unattended mode made them advisory.
- Remaining open questions.
- Blocked slices and the independent slices continued while they were blocked.
- Validation failures, repair attempts used, and any slice-owned changes preserved or reverted.
- Worker report JSON and HTML paths, assigned paths, lease/heartbeat state, changes made, validation run, blockers, proposed control-artifact changes, authority/resource evidence, and whether the coordinator accepted, rejected, requested repair, or superseded the work.
- Control index path, categorized artifact groups, and confirmation that the index is navigation-only.
- Handoff digest path, trigger, active authority, queue status, worker lease summary, supervision items, and exact resume command or path.
- Incident/exception log JSON and HTML paths, PHI-handling answer, HIPAA-aware variant status when applicable, monitoring phases, incident count, severity counts, and confirmation that no PHI, credentials, tokens, passwords, or raw secret values were stored.
- Accepted decision matrix JSON and HTML paths, accepted or superseded row count, source confirmation, and confirmation that reusable plugin defaults were not mutated.
- Dashboard data contract paths, whether a rich dashboard was requested or generated, external visualization capability used, bounded snapshot source, four-lane status summaries, visible external connection list status, and dashboard artifact path or reason none exists.
- External-capability registry paths, result-packet paths or worker-report evidence, current/superseded result-packet links, capabilities used or unavailable, authority source, autonomy boundary, supervision or credential-source boundary, spawned agents, external APIs/MCP servers/plugins/skills involved, and evidence capture.
- Chat notifications sent and any user-requested summary provided.
- Credential handling summary without credential values, if user-provided development credentials were used.
- Non-local source evidence metadata for web search, official docs, browser verification, installed plugins, or external tool output used to justify decisions.
- High-risk review status, reviewed surfaces, findings, and disposition when applicable.
- Slice queue update or reason no machine-readable queue update was needed.
- Whether the task touched docs, PHI policy, compatibility, or generated artifacts.
- Branch ledger update or reason no branch ledger update was needed.
- Supervision queue update or reason no supervision-required task exists.
- Resource gap classification: expected unavailable resources that blocked slices, and future resource opportunities that did not block work.
- Future-resource queue update or reason no nonexistent-but-useful resource was identified.
- Reversible implementation-level decisions made autonomously, with rationale.
- Decision evidence classification: `implementation`, `architecture`, or `product`, plus authority source for any higher-authority decision.

## Continuous Run Stop Policy

Stop in this order:

1. Explicit time, token, work, or budget boundary, including known platform usage limits.
2. Queue drain plus successful passing of associated tests and validation.
3. Stop condition or user-supervision boundary from `task-graph.md`.

If the run stops for a limit before queue drain, close with completed work, remaining queue items, validation already run, validation still required, and exact resume instructions.

Always write a durable HTML resume artifact under `_bmad-output/implementation-artifacts/run-state/` when stopping before queue drain. Use `delivery-workflow.md` and `_bmad-output/implementation-artifacts/run-state/template.html` for the required fields, including the per-slice status table.

Also generate a handoff digest under `_bmad-output/implementation-artifacts/handoff-digest/` at every stop, compaction-risk point, stale lease, or user-return checkpoint. The digest must include the active authority, queue status, worker leases, blockers, validation, external-capability artifacts, supervision items, and exact resume command or path.

If a stop or handoff involves an abnormal coordinator event, expected-but-unavailable resource, exhausted repair loop, stale lease, safety/privacy boundary, credential/secret exposure risk, suspicious behavior, or PHI monitoring alert, append metadata-only evidence to the incident/exception log and render the HTML review surface. PHI-capable projects must keep the HIPAA-aware log variant active and must not store PHI or secret values in the log.

## Resolved Codex Defaults

- Git autonomy: resolved. Future sessions have continuous-delivery autonomy with full unsandboxed access unless the active request narrows scope.
- Computer-use boundary: resolved. Do not engage computer-use or GUI-driving workflows without user supervision.
- Parallelism: resolved. Use one coordinator plus up to six simultaneous workers by default.
- Work source: resolved. Use BMAD artifacts first, with agent-derived safe fallback tasks only when BMAD work is empty, blocked, or silent on the next safe action.
- Stop condition: resolved. Stop first at explicit time/budget/platform usage boundaries; otherwise stop at queue drain with associated tests passing.
- Resume artifacts: resolved. Create a durable resume artifact under `_bmad-output/implementation-artifacts/` when a run stops before queue drain.
- PR workflow: resolved. PRs are non-blocking audit/synchronization artifacts, not approval gates for continuing development.
- Branch naming: resolved. Use `codex/<date>-<short-goal>` for user-facing branches and `codex/auto/<date>-<queue-slice>` for unattended queue slices.
- Branch ledger: resolved. Maintain `_bmad-output/implementation-artifacts/branch-ledger.html` with branch name, date/time last updated, and brief contents description; update it after every material branch change, not only major milestones.
- Validation and blocker handling: resolved. Keep independent parallel slices identified; blockers or failed validation stop only the affected slice and its dependents, while development continues on independent slices with a full blocked-slice report for the user.
- Run-state slice table: resolved. Include a per-slice status table with `active`, `blocked`, `waiting-on-dependent`, `validated`, `merged`, and `superseded` statuses in every HTML resume artifact.
- Slice creation: resolved. Workers may propose new independent slices, but only the autonomous coordinator creates and assigns them; user approval is not required for safe new slices inside the existing BMAD/spec boundaries.
- Machine-readable slice queue: resolved. Maintain `_bmad-output/implementation-artifacts/slice-queue.json` as source of truth so future agents can resume or dispatch workers without parsing prose.
- Human-facing artifact format: resolved. Use well-formed semantic HTML for continuous-run artifacts wherever BMAD does not require Markdown.
- Run-state renderer: resolved. Before the first real continuous run, implement and validate a renderer that derives run-state HTML from `slice-queue.json`.
- Handoff digest renderer: resolved. Generate project-local handoff digest HTML from `slice-queue.json` at every stop, compaction-risk point, stale lease, or user-return checkpoint.
- Incident/exception log: resolved. Maintain an append-only project-local JSON and semantic HTML incident log; ask whether the application handles PHI during intake; use the HIPAA-aware metadata-only variant for PHI-handling projects with monitoring phases for regular security scans and abnormal-behavior review; never store PHI, credentials, tokens, passwords, or raw secret values.
- Problem solving before blocking: resolved. Agents must exhaust authorized technical resources before stopping for ordinary difficulty.
- Escalation ladder order: resolved. The problem-solving escalation ladder is mandatory unless a time, budget, safety, permission, privacy, or task-applicability boundary prevents a step; attempts and skipped steps must be recorded.
- Browser-tool autonomy: resolved. Scoped browser/Chrome-control use is autonomous for development verification, but broad human-desktop control and external sensitive actions require supervision.
- Supervision queue: resolved. Maintain `_bmad-output/implementation-artifacts/supervision-queue.html`; user return prioritizes queued supervision tasks at the next reasonable stopping point.
- Plugin/BMAD dependency: resolved. The future plugin is BMAD-aware but not BMAD-dependent.
- Plugin/project boundary: resolved. Project-level shape, formation, guidance, decisions, queues, and run state live in project-local memory, not in reusable plugin files.
- Project overlay behavior: resolved. Projects may customize plugin output, guidance, and behavior through project-local overlays applied after plugin defaults without mutating the plugin.
- Plugin improvement queue: resolved. Reusable plugin improvement candidates are recorded in project-local HTML until the user starts an explicit plugin-development workflow.
- Instruction precedence: resolved. Resolve conflicts in order: system/developer/user safety and active request; repo-local `AGENTS.md` and explicit user project instructions; BMAD artifacts when present; project-local plugin overlay memory; plugin defaults; agent judgment.
- Overlay autonomy: resolved. Project-local overlays may broaden agent autonomy inside higher-priority boundaries because projects require different agent skill sets.
- Resource scoping language: resolved. The plugin translates user intent into needed resources and skill sets without user-facing sandbox or autonomy-profile framing.
- Missing-resource handling: resolved. Expected-but-unavailable resources stop only the affected slice with chat notification; nonexistent-but-useful resources are future opportunities and do not block work with available substitutes.
- Future-resource queue: resolved. Nonexistent-but-useful resources get their own living HTML queue at `_bmad-output/implementation-artifacts/future-resource-queue.html`.
- Future-resource promotion: resolved. Future-resource queue entries remain advisory unless the user or an explicit plugin-development workflow promotes them.
- Future-resource queue shape: resolved. Keep it as a simple chronological advisory queue with no priority/status triage.
- Reversible implementation decisions: resolved. Agents may make reversible implementation-level decisions autonomously and record the rationale in run evidence.
- Decision evidence taxonomy: resolved. Run evidence distinguishes `implementation`, `architecture`, and `product` decisions so reversible local choices are separated from higher-authority choices.
- Higher-authority decision blocking: resolved. Unresolved architecture or product decisions stop only affected slices and dependents while independent slices continue, unless the decision affects the run boundary, shared architecture, safety constraints, plugin-core mutation, PHI/privacy boundary, public API compatibility, or all remaining work.
- Validation failure repair loop: resolved. Failed implementation slices get two bounded self-repair attempts, then the affected slice stops with evidence while independent slices continue; only slice-owned changes may be preserved or reverted, never unrelated user changes or other slices.
- Shared control artifact ownership: resolved. Only the coordinator mutates shared queues, ledgers, run-state artifacts, handoff digest artifacts, project overlays, and plugin-adjacent queues; workers submit proposed control-artifact changes in reports while modifying only assigned slice files.
- Status reporting and chat notifications: resolved. Routine progress updates local artifacts; chat is reserved for expected-but-unavailable resources, exhausted repair loops, supervision-required tasks, whole-run stops, safety/privacy boundaries, user-return queue presentation, and summaries on request.
- Development credential handling: resolved. User-provided chat credentials may be used pragmatically for development tasks with best-effort non-persistence; project-local env or JSON credential sources may be used when approved; assume development and production credentials, policies, and passwords differ unless explicitly stated otherwise.
- Non-local source evidence: resolved. Web search, official docs, browser verification, installed plugins, and external tool output used to justify implementation, blockers, or architecture/product decisions require concise source metadata and official/current-enough assessment, not copied source content.
- High-risk review gate: resolved. Slices touching PHI/privacy, credentials, public API compatibility, schema/storage layout, plugin-core behavior, generated docs contracts, security boundaries, or deployment/publishing behavior require review-worker or adversarial review before merge or integration; ordinary low-risk slices may rely on tests plus coordinator review.
- Dirty-worktree intake: resolved. Every continuous run starts by classifying existing dirty paths as active slice, prior-agent work, user/unowned work, or generated artifact; unclear overlaps stop only the affected slice while independent slices continue.
- Preflight readiness gate: resolved. Before dispatch, verify spec/handoff, queue, dirty worktree, branch ledger, run-state and handoff digest renderers, validation baseline, resource/credential-source/supervision needs, and at least one safe slice or enabling slice; stop the whole run only when no independent work exists or a boundary applies.
- Plugin primary input: resolved. Normalize guided preflight interview answers, existing specs/resources, or hybrid intake into a project-local intent packet before dispatch.
- Milestone check-ins: resolved. Suggest natural user check-ins at major milestones, but keep them advisory during user-declared unattended continuous development unless a stop, safety, or supervision boundary applies.
- Intent packet forms: resolved. Maintain JSON as dispatch input and semantic HTML as the human-facing review surface.
- Domain-specific intake: resolved. Classify project archetype early and adapt questions, resources, validation, and slices to the project type.
- Design-assisted intake: resolved. For visual/frontend work, use image generation, installed design/UX skills, and/or local HTML mockups as user-selectable pathway aids and record graphic-design agreement before implementation depends on visual direction.
- Archetype registry: resolved. Use a small built-in registry for default archetype questions, design needs, validation baselines, high-risk surfaces, and slice patterns, with project-local overrides applied without mutating plugin files.
- Mixed archetypes: resolved. Treat mixed projects as first-class by choosing one primary orchestration profile and attaching secondary archetypes as scoped profiles or slices; conflicts stop only affected slices unless they affect the whole project boundary or another higher-risk shared boundary.
- Intake depths: resolved. Support quick start, standard preflight, and deep spec; unattended continuous development requires standard preflight for the primary profile, while secondary profiles need essential fields unless high-risk.
- Storage adapters: resolved. Use the BMAD adapter when `_bmad` exists and the lightweight Agentic Wiki adapter otherwise; both expose the same project-local memory primitives, including the intent packet, authority ledger, worker reports, control index, dashboard data contract, external-capability registry, run-state HTML, handoff digest HTML, incident/exception log JSON and HTML, branch ledger, supervision queue, future-resource queue, decision log, project plugin overlay, and plugin improvement queue.
- Plugin skill shape: resolved. Use one reusable plugin with focused skills for intake/preflight, continuous-run coordination, status/report handoff, and plugin-improvement review; shared schemas/templates/scripts are plugin-owned while project-specific output goes through the selected storage adapter.
- Skill mutation permissions: resolved. Intake/preflight writes intent/readiness/queue/resource artifacts only, coordinator may edit project implementation and shared control artifacts, status/report handoff is read-only except generated reports, and plugin-improvement review writes candidate analysis without touching plugin files unless explicit plugin development starts.
- Plugin run modes: resolved. Intent packets record `interactive-intake`, `unattended-continuous-run`, `status-only-report`, or `plugin-improvement-review`; mode may be inferred when safe, unattended mode requires standard preflight, status-only is read/report-only, and plugin-improvement review cannot mutate plugin files without explicit plugin-development work.
- Run-mode transitions: resolved. Narrowing transitions may happen autonomously; broadening transitions require user intent, recorded continuous-run directive, standard preflight, or explicit plugin-development work depending on the transition.
- Authority ledger: resolved. Maintain synchronized project-local JSON and semantic HTML authority ledger forms recording active directives, inferred permissions, run modes, transitions, continuous-run directives, expiration or supersession notes, and evidence for broadening-transition authority.
- Authority-ledger append-only policy: resolved. Add new ledger source entries for new directives, inferred permissions, expirations, and supersessions; do not rewrite prior source entries in place, while generated views may derive current-active status from later entries.
- Worker reports: resolved. Each worker produces a slice-local JSON and semantic HTML worker report before coordinator acceptance or rejection, including assigned paths, changes made, validation run, blockers, proposed shared-control-artifact changes, and authority or resource evidence.
- Worker lease model: resolved. Each active slice records worker ID, branch, assigned paths, lease or heartbeat timestamp, and stale-worker handling; stale leases are marked and inspected by the coordinator before reassignment or supersession without blocking independent slices.
- Control index: resolved. Maintain a navigation-only semantic HTML control index linking current agent-development artifacts and categorized HTML files.
- Rich dashboards: resolved. Support optional per-project interactive dashboards by preparing bounded project-local dashboard data and coordinating with external visualization plugins or tools such as `build-web-data-visualization`; do not hard-code every dashboard model into the reusable plugin.
- External capability coordination: resolved. The coordinator may use and spawn work through external plugins, skills, APIs, MCP servers, browser tools, external services, and agents inside authority, autonomy, supervision, credential-source, privacy, and evidence boundaries.
- External capability autonomy and credential source: resolved. External capability calls are autonomous only inside active authority and supervision boundaries; project-local credential sources may be env or JSON files with provider-specific payloads, and durable artifacts record metadata only.
- External capability result packets and dashboard-visible connections: resolved. Each external capability invocation is captured in a worker report or JSON/HTML result packet with replay/audit metadata and no secrets; dashboard data includes visible external connection lists for APIs, MCP servers, plugins, skills, browser tools, services, local tools, and spawned agents.
- External capability result-packet immutability: resolved. Accepted result packets are immutable; corrections create superseding packets linked to the prior packet, and dashboards display the current packet while preserving history links.
- Dashboard status lanes: resolved. Status/dashboard data separates internal agent work, external capability activity, validation/evidence, and supervision-required items into distinct lanes with current, blocked, completed, and last-updated summaries, while retaining a combined top-level project status.
- Accepted decision matrix: resolved. User-approved accelerated interview decisions live in project-local JSON and semantic HTML matrix artifacts; future agents consult them before reopening questions, and plugin defaults change only through explicit plugin-development work.
