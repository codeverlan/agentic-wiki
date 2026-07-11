# Delivery Workflow

## Purpose

Define how continuous Codex runs deliver work, use pull requests, and preserve resumable state without turning human approval into a development bottleneck.

## Delivery Rule

Continuous development is gated by BMAD artifacts, repo constraints, validation results, and stop conditions. It is not gated by traditional pull-request review unless the active user request explicitly narrows autonomy.

## Dirty Worktree Intake

Before dispatching workers or creating implementation slices, the coordinator runs `git status --short --branch` and classifies every existing dirty path.

Use these classifications:

- `active slice`: known current work owned by the coordinator or assigned slice.
- `prior-agent work`: earlier agent-created work that is not yet integrated or superseded.
- `user/unowned work`: user-authored or unclear ownership that must be preserved.
- `generated artifact`: generated docs, run-state, handoff digest, incident log, queue, ledger, or other rebuildable output.

If a planned slice overlaps a user/unowned or otherwise unclear dirty path, stop that slice, record the overlap as a slice-local blocker, and choose an independent slice when one exists. Do not stop the whole run solely because the tree is dirty. Continue work only on paths whose ownership is classified and compatible with the active slice.

Record the intake in run evidence with path, git status, classification, owner or slice, action, and notes. Workers receive the resulting forbidden paths and must not touch unowned dirty paths.

## Preflight Readiness Gate

Before worker dispatch or unattended continuous development, the coordinator must pass a preflight readiness gate.

Verify:

- The active spec or project-local intent packet JSON is loaded.
- The human-facing semantic HTML intent packet exists or can be generated for user/review visibility.
- The project-local storage adapter is selected and writable: BMAD adapter when `_bmad` exists, lightweight Agentic Wiki adapter otherwise.
- The current plugin workflow skill is selected for this task: intake/preflight, continuous-run coordination, status/report handoff, or plugin-improvement review.
- The selected plugin workflow skill's mutation permission matches the planned writes.
- The plugin run mode is selected or safely inferred: interactive-intake, unattended-continuous-run, status-only-report, or plugin-improvement-review.
- Run-mode requirements are satisfied: unattended continuous run has standard preflight for the primary profile, status-only report is read/report-only except generated reports, and plugin-improvement review has not mutated plugin files unless explicit plugin development has started.
- Any run-mode transition is classified as narrowing or broadening, with authority recorded before broadening behavior.
- The authority ledger JSON and HTML are loaded or can be generated, and active directives are checked before broadening transitions or unattended dispatch.
- Worker-report JSON and HTML templates are loaded or can be generated before worker dispatch.
- The control index exists or can be generated, and it links current project-local control artifacts without becoming a source of truth.
- The handoff digest template exists or can be generated, and the latest digest can be rendered from the slice queue for stops, compaction risk, stale leases, and user-return checkpoints.
- The incident/exception log JSON and HTML surfaces exist or can be generated, and the PHI-aware variant is selected when the application handles PHI.
- The accepted decision matrix JSON and semantic HTML surfaces exist or can be generated, and future agents consult them before reopening settled interview questions.
- Dashboard data, external-capability registry, and external-capability result templates exist or can be generated when the project needs rich status dashboards or external capability coordination.
- Intake depth is selected, and standard preflight is satisfied for the primary profile when unattended continuous development is requested.
- The archetype registry JSON and HTML review surface are loaded, and the primary orchestration profile plus any secondary scoped profiles are recorded or an enabling question is queued.
- The handoff/runbook and repo-local instructions are loaded.
- The slice queue exists or can be created and parses successfully.
- Dirty-worktree intake is complete.
- Branch ledger status is current for the active branch.
- The run-state, handoff digest, and incident-log renderers are available before a real continuous run.
- The intake packet asks whether the application will handle PHI, and PHI-handling projects have security monitoring phases for regular scans and abnormal-behavior review.
- Validation baseline is selected for the planned work.
- Resource, credential-source, and supervision needs are identified, including whether any project-local env or JSON credential source is required and local-only.
- Project archetype is classified or an enabling question is recorded.
- Design pathway status is recorded when visual or frontend work is material.
- At least one safe slice is ready, or an enabling slice is identified.
- Suggested check-in points are recorded without turning them into approval gates for unattended work.

If the gate fails, create an enabling slice or blocker entry. Stop the whole run only when no independent work is available or a stop, safety, or supervision boundary applies.

## Storage Adapter Model

The future plugin uses storage adapters to choose where project-local memory lives:

- BMAD adapter: selected when `_bmad` exists; writes project-local primitives into BMAD-compatible `_bmad-output` surfaces.
- Lightweight Agentic Wiki adapter: selected when `_bmad` is absent; creates a neutral project-local memory root with the same primitives.

Both adapters expose the same logical primitives: intent packet, authority ledger, accepted decision matrix, worker reports, control index, dashboard data contract, external-capability registry, external-capability result packets, run-state HTML, handoff digest HTML, incident/exception log JSON and HTML, branch ledger, supervision queue, future-resource queue, decision log, project plugin overlay, and plugin improvement queue. Adapter choice does not alter instruction precedence, plugin immutability, safety boundaries, or the normalized work contract.

## Plugin Skill Shape

The future plugin is one reusable plugin with multiple focused skills:

- Intake/preflight: normalizes user intent into an intent packet and selects storage adapter, intake depth, archetype profiles, PHI-handling posture, resources, validation, and supervision needs.
- Continuous-run coordinator: manages readiness, slice queue, workers, validation, blockers, repair attempts, branch ledger, run-state, handoff digests, incident logs, and continuous delivery.
- Status/report handoff: produces status summaries, user-return reports, run-state/resume surfaces, handoff digests, incident summaries, and supervision or resource queue presentations from project-local memory.
- Plugin-improvement review: reviews plugin-improvement candidates only after the user starts an explicit plugin-development workflow.

Shared schemas, templates, and scripts may live in the plugin. Project-specific decisions, queues, overlays, reports, and run state are written through the selected storage adapter.

Skill-level mutation permissions:

- Intake/preflight writes intent, readiness, queue, and resource artifacts only; it does not edit project code.
- Continuous-run coordinator may edit code, docs, tests, fixtures, and shared control artifacts within the active request and project boundaries.
- Status/report handoff is read-only except for generated project-local reports, summaries, resume surfaces, and user-return presentations.
- Plugin-improvement review writes project-local candidate analysis and queue updates only; plugin files remain immutable unless the user explicitly starts plugin-development work.

If a selected skill would need to exceed its mutation permission, reroute to the appropriate skill or stop the affected slice with evidence.

## Plugin Run Modes

Run mode describes the operational posture for the current task:

- `interactive-intake`: ask focused questions, inspect referred resources, and create or repair intent, readiness, queue, resource, and supervision artifacts.
- `unattended-continuous-run`: run autonomous queue work after standard preflight is satisfied for the primary profile; continue independent slices until a stop boundary, safety boundary, validation exhaustion, or queue drain.
- `status-only-report`: read current project-local memory and generate status reports, summaries, user-return presentations, and resume surfaces without editing project code or planning source artifacts.
- `plugin-improvement-review`: review plugin-improvement candidates and write project-local candidate analysis or queue updates; plugin files remain immutable unless the user explicitly starts plugin-development work.

The plugin may infer run mode from user intent when safe. If the inferred mode would change mutation authority, unattended behavior, supervision needs, or plugin-file risk, record an enabling question or stop only the affected slice until the mode is clear.

## Run Mode Transitions

Narrowing transitions may happen autonomously when they reduce authority or scope, such as switching from `unattended-continuous-run` to `status-only-report` for user-return presentation, blocker reporting, validation-exhaustion reporting, or a user-requested summary.

Broadening transitions require clear authority:

- `status-only-report` to `unattended-continuous-run` requires user intent or a still-applicable recorded continuous-run directive.
- `interactive-intake` to `unattended-continuous-run` requires standard preflight for the primary profile.
- `plugin-improvement-review` to plugin file changes requires explicit plugin-development work started by the user.

If authority is missing, stop or reroute only the affected slice and continue independent safe work.

## Authority Ledger

Maintain a project-local authority ledger at the selected storage adapter's mapped JSON and semantic HTML paths. The ledger records active user directives, inferred permissions, selected run modes, mode transitions, continuous-run directives, expiration or supersession notes, and authority evidence used for broadening transitions.

Before broadening behavior or dispatching unattended work, the coordinator checks the ledger for a still-applicable directive or permission. If the ledger is missing or stale but can be generated from current project artifacts, create or repair it as an enabling slice. If no authority exists for the broadening transition, stop or reroute only the affected slice and continue independent safe work.

Ledger source entries are append-only. To expire or supersede a directive, append a new expiration or supersession entry that references the prior entry ID. Do not edit earlier source entries in place. Generated HTML views may derive current-active status from later entries for readability.

## Intake Depths

The plugin supports three intake depths:

- Quick start: minimum viable intent packet for small, clear, low-risk work.
- Standard preflight: default depth for implementation and continuous delivery; enough context to support unattended work, validation planning, resource scoping, and independent slice derivation.
- Deep spec: full BMAD-style product, UX, architecture, and implementation-readiness depth for large, risky, unclear, or high-dependency projects.

Use standard preflight as the minimum for the primary orchestration profile before unattended continuous development. Secondary profiles in mixed projects require essential fields only unless they become high-risk, affect shared architecture, or define a major user-facing surface. If the active intent is still at quick-start depth and the user requests unattended continuous development, create an enabling preflight slice before worker dispatch.

## Milestone Check-Ins

The plugin may suggest natural user check-in points at major milestones such as initial intent packet readiness, project-archetype classification, visual direction selection, architecture/product decision boundaries, high-risk review completion, queue phase completion, validation failure after repair attempts, PR or branch synchronization, and queue drain.

Suggested check-ins are advisory during unattended continuous development. When the user indicates they will be away for a long period and asks for continuous development, the coordinator prioritizes safe queue progress, updates local artifacts, and stops only at explicit stop, safety, supervision, validation exhaustion, or no-independent-work boundaries.

## Status Reporting And Chat Notifications

During unattended or long-running development, routine progress belongs in local artifacts, not chat. The coordinator updates `slice-queue.json`, run-state HTML, handoff digest HTML when a digest trigger occurs, incident-log JSON/HTML when abnormal conditions occur, branch ledger, worker reports, and memlog as the normal status stream.

Use chat notifications only for:

- Expected-but-unavailable resources that stop an affected slice.
- Exhausted validation repair loops.
- Supervision-required tasks.
- Whole-run stops.
- Safety or privacy boundaries.
- User-return queue presentation.
- A user-requested status summary.

When the user asks for a summary, produce it from the current local artifacts and include active slice status, blocked slices, validation state, branch state, supervision/resource queue items, and exact next action. Do not treat a summary request as approval to interrupt safe active work unless the user explicitly asks to stop or redirect.

## Incident And PHI Monitoring Log

Maintain the project-local incident and exception log under:

```text
_bmad-output/implementation-artifacts/incident-log/
```

The JSON form is the append-only source of truth. The semantic HTML form is the human-facing review surface. Use the log for abnormal coordinator events, expected-but-unavailable resources, exhausted repair loops, stale leases, safety or supervision stops, whole-run stops, suspicious behavior, credential/secret exposure risk, and PHI monitoring alerts.

The intake flow must expressly ask whether the application will handle PHI. If yes, select the HIPAA-aware metadata-only log variant. That variant must:

- Record incident IDs, timestamps, severity, category, evidence pointers, monitoring phase, and next action.
- Avoid PHI, credentials, tokens, passwords, raw secret values, and raw sensitive text.
- Include monitoring phases that remind the user to run dependency, secret, static-analysis, and access-review scans.
- Include abnormal-behavior monitoring for possible leakage of information, credentials, plans, or PHI.
- Record HIPAA-aware software-development best practices without presenting the artifact as legal certification or a substitute for organizational HIPAA compliance operations.

## Development Credential Handling

If the user chooses to pass a credential in chat for development work, agents may use it for the approved task. Treat that as a pragmatic development workflow, not as a reason to stop the slice solely because the credential appeared in chat.

Credential handling rules:

- Make best efforts not to store, echo, log, commit, or persist the credential value.
- Do not write credential values into BMAD artifacts, Agentic Wiki memory, run-state HTML, handoff digest HTML, incident logs, queue files, branch ledger, worker reports, tests, fixtures, docs, diffs, commits, or PR text.
- If an artifact must mention credential use, record only that a user-provided development credential was used or needed; never include the value.
- Prefer ephemeral use through the least persistent available mechanism for the task.
- Do not create persistent credential files, env files, account links, or saved sessions unless the user explicitly asks for that setup or the project's approved intent packet requires a local credential source.
- When local credential setup is approved, credentials live in the target project as a local env file, local JSON file, process environment, or other project-local secret source selected for that capability. The reusable plugin records only metadata and never stores the credential value.
- Do not standardize credential payload shape in the reusable plugin. The local source may hold API keys, usernames/passwords, OAuth tokens, service-specific JSON, browser-session references, or other provider-specific auth material as the target project requires.
- Credential source metadata may include local path, owning capability, auth scheme label, rotation note, loaded variable/key names when non-sensitive, and local-only/gitignore expectation; it must not include secret values.
- Assume the current work is development and that production credentials, policies, and passwords differ from development unless the user explicitly says otherwise.
- Production credential use, production permission changes, personal login, account linking, sensitive external actions, or unclear authorization still require explicit user direction or supervision.

## Non-Local Source Evidence

When an agent uses web search, current official docs, browser verification, installed plugins, external tool output, or any other non-local source to justify implementation, blocker classification, or architecture/product decisions, run evidence must record metadata rather than copying large source content.

Record:

- Source URL, documentation page, tool name, plugin name, or browser verification surface.
- Retrieval or check time in ISO 8601 local time when available.
- Claim, decision, blocker, or implementation choice the source supported.
- Whether the source was official, primary, current enough, or otherwise acceptable for the decision.
- Any material limitation, such as stale docs, inaccessible page, partial browser check, or unofficial source.

Do not paste large external source content into run-state artifacts, handoff digests, worker reports, blocker notes, or memlog. Keep evidence concise and citeable.

## High-Risk Review Gate

Before merge or integration, require review-worker or adversarial review for slices touching:

- PHI/privacy.
- Credentials.
- Public API compatibility.
- Schema or storage layout.
- Plugin-core behavior.
- Generated docs contracts.
- Security boundaries.
- Deployment or publishing behavior.

The coordinator records the review result in run evidence before merging, integrating, or superseding the slice. If no review worker is available, stop the affected high-risk slice and continue independent slices unless the review gap affects all remaining work.

Ordinary low-risk implementation slices may proceed with focused tests, required validation, and coordinator review.

## Shared Control Artifact Ownership

Only the coordinator may mutate shared control artifacts:

- `_bmad-output/implementation-artifacts/slice-queue.json`.
- `_bmad-output/implementation-artifacts/authority-ledger/*.json`.
- `_bmad-output/implementation-artifacts/authority-ledger/*.html`.
- `_bmad-output/implementation-artifacts/branch-ledger.html`.
- `_bmad-output/implementation-artifacts/run-state/*.html`.
- `_bmad-output/implementation-artifacts/handoff-digest/*.html`.
- `_bmad-output/implementation-artifacts/incident-log/*.json`.
- `_bmad-output/implementation-artifacts/incident-log/*.html`.
- `_bmad-output/implementation-artifacts/accepted-decisions/*.json`.
- `_bmad-output/implementation-artifacts/accepted-decisions/*.html`.
- `_bmad-output/implementation-artifacts/archetype-registry/*.json`.
- `_bmad-output/implementation-artifacts/archetype-registry/*.html`.
- `_bmad-output/implementation-artifacts/supervision-queue.html`.
- `_bmad-output/implementation-artifacts/future-resource-queue.html`.
- `_bmad-output/implementation-artifacts/plugin-improvement-queue.html`.
- `_bmad-output/implementation-artifacts/project-plugin-overlay.html`.

Workers modify only assigned code, docs, tests, fixtures, their own slice-local worker report, or approved slice-local artifacts. When a worker discovers a needed queue, ledger, run-state, handoff digest, incident-log, accepted-decision, supervision, resource, plugin-improvement, or overlay update, the worker records the proposed change in its worker report. The coordinator reviews worker evidence and validation before accepting the proposed control-artifact change.

## Worker Reports

Maintain slice-local worker reports under:

```text
_bmad-output/implementation-artifacts/worker-reports/
```

Each worker report has synchronized JSON and semantic HTML forms. The worker report is required before the coordinator accepts, rejects, merges, or supersedes worker output.

The report must include:

- Worker ID and slice ID.
- Worker lease ID, heartbeat timestamp, lease status, stale-after timestamp, stale handling, and reassignment or supersession note when applicable.
- Assigned paths and forbidden paths.
- Files changed and files intentionally not touched.
- Summary of changes made.
- Validation commands run and results.
- Blockers or dependency discoveries.
- Dirty-path overlaps discovered or avoided.
- Proposed shared-control-artifact changes.
- Authority ledger entries, resource evidence, or non-local source evidence used.
- High-risk review status or reason the slice is low-risk.
- Coordinator disposition: pending, accepted, rejected, needs repair, or superseded.

Workers may write only their own slice-local worker report and assigned slice files. Proposed queue, ledger, run-state, handoff digest, incident-log, supervision, future-resource, plugin-improvement, or overlay updates in the report are not applied until the coordinator accepts them after evidence and validation review.

## Worker Leases

The coordinator records active worker assignments as project-local leases on the slice queue. A lease records worker ID, branch, assigned paths, heartbeat timestamp, lease status, stale-after timestamp, stale handling, and reassignment or supersession notes.

Workers refresh their heartbeat through their worker report or a coordinator-accepted queue update. If a heartbeat becomes stale, the coordinator marks only that lease stale, inspects and preserves slice-owned work, records evidence, and either reassigns the slice or creates a superseding slice. Stale leases do not block unrelated independent slices.

## Control Index And Dashboards

Maintain the project-local control index at:

```text
_bmad-output/implementation-artifacts/control-index.html
```

The control index is a semantic HTML navigation surface, not a source of truth. It links the current intent packet, authority ledger, accepted decision matrix, slice queue, run-state, handoff digest, incident/exception log, branch ledger, supervision queue, future-resource queue, plugin-improvement queue, worker reports, dashboard data contract, external-capability registry, external-capability result packets, and categorized HTML artifacts.

Maintain dashboard data contract templates at:

```text
_bmad-output/implementation-artifacts/dashboard-data/
```

Dashboard data contracts provide bounded project-local snapshots for optional rich dashboards. They should include status, agent activity, pending/active/completed tasks, accepted decisions, token use when available, validation state, blockers, supervision/resource queues, worker reports, external connections, external capability result packets, status-lane summaries, and categorized HTML files. The dashboard data contract is not itself a visualization model.

Dashboard status data separates project activity into four lanes:

- `internal_agent_work`: coordinator, workers, spawned local agents, slice movement, and internal task execution.
- `external_capability_activity`: plugins, skills, APIs, MCP servers, browser tools, external services, local tools, spawned external-resource work, and result packets.
- `validation_evidence`: validation commands, review evidence, test results, non-local source evidence, and repair-loop state.
- `supervision_required`: queued human-supervised actions, user-return items, sensitive actions, credential/account boundaries, and explicit approval needs.

Each lane records current, blocked, completed, and last-updated summaries. The dashboard may still display one combined top-level project status, but the underlying data must keep these lanes distinct so users can tell where progress, blockers, and supervision needs came from.

Rich interactive dashboards may be generated by external capabilities such as `build-web-data-visualization`, Data Analytics widgets, custom web apps, or project-specific tools. The coordinator prepares bounded project-local data and records the external capability used, inputs, generated artifact path, limitations, and validation evidence. Generated dashboards must make external connections visible by name/type/status/evidence link while omitting credentials and secret values. Generated dashboards remain project-local artifacts unless the user explicitly asks to publish or deploy them.

## External Capability Coordination

Maintain the external-capability registry at:

```text
_bmad-output/implementation-artifacts/external-capabilities/
```

Maintain external-capability result packets at:

```text
_bmad-output/implementation-artifacts/external-capability-results/
```

The coordinator may call, route to, or spawn work through external plugins, skills, APIs, MCP servers, browser tools, external services, and subagents when a project needs capabilities beyond the reusable plugin's built-in surfaces. The plugin should coordinate these capabilities rather than hard-code every future function. External capability calls are autonomous when they stay inside active project authority, development/local boundaries, and existing supervision rules; credentials, production changes, publishing, personal-account actions, broad computer-use, sensitive data access, or unclear authorization require the applicable explicit direction or supervision.

Each external capability entry records:

- Capability ID, type, name, and reference.
- Purpose and related slices.
- Availability status and expected-unavailable versus future-opportunity classification.
- Authority source, autonomy boundary, supervision needs, credential-source metadata, and privacy boundary.
- Inputs, outputs, evidence capture, limitations, fallback, worker-report path when delegated to an agent, and result-packet paths when the capability is invoked.

Each external capability invocation must be auditable through either:

- A slice-local worker report entry that includes the capability, source, checked time, supported decision, limitations, and result summary.
- A dedicated JSON/HTML result packet under `_bmad-output/implementation-artifacts/external-capability-results/`.

Result packets record invocation ID, capability ID, connection type/name/reference, related slice, caller, checked time, authority source, autonomy/supervision boundary, credential-source metadata without values, bounded input summary, bounded output summary, artifacts produced, provenance, replay notes, limitations, lifecycle state, supersession links, and dashboard visibility. Do not store secrets, credential values, hidden reasoning, full prompts when sensitive, large raw responses, PHI, or oversized payloads.

After coordinator acceptance, result packets are immutable source records. If a packet is wrong, incomplete, stale, or superseded by a rerun, create a new result packet whose `supersedes_result_id` and prior-packet link point to the accepted packet. Do not edit the accepted packet in place. Dashboard data and generated dashboards display the current accepted packet and preserve history links to superseded packets.

External capability use must honor the authority ledger, skill mutation permissions, credential handling rules, project-local credential-source rules, supervision boundaries, non-local source evidence requirements, and resource-gap handling. Expected-but-unavailable capabilities stop only affected slices with chat notification. Useful nonexistent capabilities are recorded in the future-resource queue and do not block work with available substitutes.

## Plugin And Project Memory Boundary

The future reusable plugin provides machinery, defaults, schemas, templates, and commands. It must not become the storage location for project-specific shape, formation, guidance, queues, run state, or decisions.

When `_bmad` exists, BMAD artifacts remain the project planning/source-of-truth layer. When `_bmad` is absent, use the plugin's lightweight project-local memory with equivalent surfaces. In both cases, project-specific outputs live in the target project or configured memory workspace.

Project-specific plugin behavior is implemented through project-local overlays applied after plugin defaults. Do not mutate plugin installation files, plugin cache files, marketplace entries, global skills, or plugin defaults during ordinary project development.

Maintain these project-local HTML artifacts:

- `_bmad-output/implementation-artifacts/archetype-registry/template.html` for default archetype review and project-local overrides.
- `_bmad-output/implementation-artifacts/authority-ledger/template.html` for active directives, inferred permissions, mode transitions, continuous-run directives, and broadening-transition evidence.
- `_bmad-output/implementation-artifacts/worker-reports/template.html` for slice-local worker evidence and proposed shared-control-artifact changes before coordinator acceptance.
- `_bmad-output/implementation-artifacts/control-index.html` for navigation across project-local agent-development artifacts.
- `_bmad-output/implementation-artifacts/handoff-digest/template.html` for stop, compaction-risk, stale-lease, and user-return digest generation.
- `_bmad-output/implementation-artifacts/incident-log/template.html` for metadata-only incident/exception logging and PHI-aware monitoring phases.
- `_bmad-output/implementation-artifacts/dashboard-data/template.html` for dashboard data contract review before external visualization generation, including four-lane status summaries.
- `_bmad-output/implementation-artifacts/external-capabilities/template.html` for project-scoped external capability coordination.
- `_bmad-output/implementation-artifacts/external-capability-results/template.html` for auditable external invocation result packets and dashboard-visible connection rows.
- `_bmad-output/implementation-artifacts/project-plugin-overlay.html` for project-specific output, guidance, and behavior overlays.
- `_bmad-output/implementation-artifacts/plugin-improvement-queue.html` for reusable plugin improvement candidates.

Record reusable plugin improvement candidates in project-local memory until the user explicitly starts a plugin-development workflow.

## Instruction And Overlay Precedence

When continuous delivery guidance conflicts across active user direction, repo rules, BMAD artifacts, project-local overlays, plugin defaults, or agent judgment, resolve it in this order:

1. System/developer/user safety and the active user request.
2. Repo-local `AGENTS.md` and explicit user project instructions.
3. BMAD artifacts, when present.
4. Project-local plugin overlay memory.
5. Plugin defaults.
6. Agent judgment.

Project-local overlays are applied after plugin defaults only for behavior still allowed by higher-priority instructions. They may narrow, specialize, or broaden autonomy inside those boundaries so each project can enable the agent skill sets it needs. If an overlay appears to conflict with a higher-priority source, record the conflict in run-state evidence or the supervision queue instead of silently following the overlay.

## Intent-To-Resource Scoping

The plugin should translate user intent into the resources needed for the project and current task: agent skill sets, tool classes, worker shape, validation depth, escalation resources, and supervision-required actions.

Do not present this as sandboxing or as an autonomy profile. Human-facing output should explain what resources the work needs and what requires supervision, using project-fit language.

The input to this scoping process is a project-local intent packet stored through the selected project-local storage adapter. The packet may come from quick-start intake, standard preflight, deep spec, existing specs/resources referred by the user, or a hybrid intake where the plugin asks only the additional questions needed to make the work actionable.

The intent packet has two synchronized forms:

- JSON dispatch input for automation.
- Semantic HTML review surface for the user and future agents.

The plugin must classify the project archetype early and select the appropriate interview path. For example, a WordPress website needs questions about content model, theme/block strategy, plugins, editorial workflow, hosting, SEO, accessibility, and publishing; a web application needs questions about app flows, data model, auth, state, API contracts, environments, testing, deployment, observability, and product acceptance.

Use the built-in archetype registry as the default source for discriminating questions, likely design needs, validation baseline, high-risk surfaces, and common slice patterns. Project-local registry overrides apply after plugin defaults and must be recorded in project-local memory rather than plugin files.

For mixed projects, choose one primary orchestration profile for the top-level intake path, queue shape, and milestone framing. Attach secondary archetypes as scoped profiles or slices. Profile conflicts stop only the affected slice and dependents unless they change the whole project boundary, shared architecture, safety boundary, plugin-core behavior, PHI/privacy boundary, public API compatibility, or all remaining work.

When visual or frontend work is material, the plugin should use image generation, installed design/UX skills, and local HTML mockups to suggest visual pathways. These are project-local decision aids; selected or deferred design directions are recorded in the intent packet and downstream UX/design artifacts.

## Resource Gap Handling

When a needed resource is not currently available, classify it before deciding whether to block:

- Expected resource unavailable: the resource should be available for the current slice but is not. Stop that slice, notify the user in chat, record the blocker in run-state evidence, and continue independent slices when available.
- Future resource opportunity: the resource does not exist yet but would help future work. Continue with available resources, record it in the future-resource queue, and note the opportunity to the user; record reusable plugin opportunities in the plugin-improvement queue when evidence is concrete.

Maintain the living future-resource queue at:

```text
_bmad-output/implementation-artifacts/future-resource-queue.html
```

The queue must be well-formed semantic HTML with JSON-LD metadata and one simple chronological table containing:

- Opportunity ID.
- Date/time added.
- Source slice or task.
- Useful resource that does not exist yet.
- Why it would help.
- Available substitute used now.
- Advisory note.

This queue is for future opportunities only. Expected-but-unavailable resources belong in blocker notes and run-state evidence. Future-resource entries remain advisory until explicitly promoted by the user or an explicit plugin-development workflow. Do not add priority or status triage fields.

## Decision Authority During Runs

Agents may make reversible implementation-level decisions autonomously when the choice stays inside current requirements, repo constraints, validation expectations, and safety boundaries.

Classify run decisions as:

- `implementation`: reversible local coding, test, file-organization, or integration choices that stay inside current requirements. Agents may decide and record these autonomously.
- `architecture`: choices that affect shared system structure, data model, module boundaries, runtime/dependency strategy, compatibility surfaces, security posture, deployment shape, or plugin-core structure. These need an existing BMAD/spec/user authority source or must be treated as higher-authority open decisions.
- `product`: choices that affect user-facing scope, behavior, acceptance criteria, prioritization, or value proposition. These need an existing BMAD/spec/user authority source or must be treated as higher-authority open decisions.

Record each such decision in run evidence with:

- The decision type: `implementation`, `architecture`, or `product`.
- The decision made.
- The rationale.
- The authority source for architecture or product decisions.
- Why it is reversible.
- Tests or validation that cover the choice.

Do not use this rule for product-scope, public API compatibility, PHI/privacy, irreversible architecture, external production, or plugin-core mutation decisions; those remain governed by blocker, supervision, and explicit-approval rules.

When an unresolved `architecture` or `product` decision appears mid-run, stop the affected slice and its dependents, record the open decision, and continue independent slices. Pause the whole run only when the decision affects the run boundary, shared architecture, safety constraints, plugin-core mutation, PHI/privacy boundary, public API compatibility, or all remaining work.

## PR Semantics

- PRs are audit, synchronization, and visibility artifacts.
- Opening a PR is not a stop condition.
- Waiting for user approval is not required before continuing development.
- Validation, BMAD acceptance criteria, PHI safety, compatibility preservation, and docs checks are the quality gates.
- If validation passes and repo policy allows agent-managed merge, Codex may merge and continue.
- If branch protection, missing credentials, unavailable remotes, or platform policy blocks merge, record the blocker and continue safe work on the active branch or a follow-on branch.
- If tests fail, keep the PR draft/WIP or equivalent, create a repair task, and continue unless a time/budget or stop-condition boundary prevents it.

## Validation Failure Repair Loop

When validation fails after an implementation slice:

1. Treat repair as the highest-priority task for that slice.
2. Attempt at most two bounded self-repairs inside the same slice.
3. For each attempt, record the failing command, failure summary, fix hypothesis, touched paths, validation rerun, and result.
4. If either attempt passes, continue normal validation and delivery for that slice.
5. If the second repair attempt fails, stop the slice, record failure evidence, mark dependent slices as waiting, and continue independent slices.
6. Preserve or revert only slice-owned changes as appropriate; revert them only when they destabilize the workspace, block independent slices, or are clearly not useful for diagnosis.
7. Never roll back unrelated user changes, unrelated dirty-tree changes, or changes owned by other slices.

## Branch And Commit Flow

1. Start from the active branch/worktree state.
   - Run dirty-worktree intake and classify existing dirty paths before assigning slices or editing files.
2. Pass the preflight readiness gate or create the enabling/blocker slice that makes the gate actionable.
3. Create a task branch when useful for isolation or remote delivery.
   - Use `codex/<date>-<short-goal>` for user-facing runs.
   - Use `codex/auto/<date>-<queue-slice>` for unattended queue slices.
4. Commit coherent validated slices.
5. Push and open or update a PR when a remote is configured and doing so improves auditability or handoff.
6. Continue the BMAD queue or safe fallback queue after PR creation.
7. Merge automatically when validation passes and repo policy permits agent-managed merge.
8. If merge is blocked externally, preserve progress in commits, PR notes, and the resume artifact, then continue independent work.
9. Update the branch ledger after branch creation, material branch changes, PR creation/update, merge, or external merge block.

## Branch Ledger

Maintain the living branch ledger at:

```text
_bmad-output/implementation-artifacts/branch-ledger.html
```

The ledger must be semantic HTML and include one table with these columns:

- Branch name.
- Date/time last updated.
- Brief description of contents.

Use ISO 8601 local time with timezone for updates. Keep descriptions short and factual, naming the primary work slice, PR/merge state when relevant, and whether the branch is active, merged, blocked, or superseded.

Update cadence is every material branch change, not only major milestones. A material branch change includes branch creation, commits added, PR opened or updated, merge completed, merge blocked, branch superseded, or a meaningful change in branch contents or status.

## Durable Resume Artifact

When a run stops before queue drain, write a well-formed semantic HTML resume artifact under:

```text
_bmad-output/implementation-artifacts/run-state/<timestamp>-<slug>.html
```

Use `_bmad-output/implementation-artifacts/run-state/template.html` as the starting shape. HTML run-state artifacts are human-facing summaries derived from `slice-queue.json`; do not make them the source of truth for active slice state.

The artifact must include semantic sections for:

- Run objective.
- Intent packet JSON source, HTML review source, project archetype, design pathway status, and preflight readiness result.
- Authority ledger JSON source, HTML review source, active directive status, transition authority used, and expiration or supersession notes.
- Stop reason: time, token, work budget, platform usage limit, stop condition, or external blocker.
- Active branch, commit SHA, remote, PR URL or reason no PR exists.
- Branch ledger status.
- Per-slice status table.
- Dependency or flow diagram when useful.
- Completed tasks.
- Remaining BMAD queue items.
- Safe fallback queue items.
- Blockers and required user decisions.
- Unresolved architecture or product decisions that stopped affected slices, plus whether independent slices continued or the whole run paused.
- Files changed.
- Tests and validation run.
- Tests and validation still required.
- Non-local source evidence used for implementation, blockers, or architecture/product decisions.
- High-risk review status and review-worker or adversarial findings when applicable.
- Decision evidence classified as `implementation`, `architecture`, or `product`, including rationale, authority source, reversibility, and validation.
- Worker summaries if multiple agents were used.
- Worker reports and proposed control-artifact changes accepted or rejected by the coordinator.
- Incident/exception log path, PHI-handling answer, HIPAA-aware variant status when applicable, monitoring phases, incident count, severity counts, and confirmation that no PHI, credentials, tokens, passwords, or raw secret values were stored.
- Accepted decision matrix JSON and HTML paths, accepted or superseded row count, source confirmation, and confirmation that reusable plugin defaults were not mutated.
- Chat notifications sent and user-requested summaries provided.
- Suggested milestone check-ins and whether they were advisory, reached, skipped, or supervision-required.
- Supervision queue status and any tasks awaiting the returned user.
- Exact resume instructions for the next Codex thread.

The HTML may use CSS color, badges, tables, lists, diagrams, and reference links to make status clear. Keep the content local and static; do not require remote scripts or external assets.

## Handoff Digest

Maintain project-local handoff digests under:

```text
_bmad-output/implementation-artifacts/handoff-digest/
```

Generate a handoff digest at every run stop, compaction-risk point, stale lease, or user-return checkpoint. The digest is rendered from `slice-queue.json` and is not the source of truth.

Each digest must include:

- Current objective and trigger.
- Active authority or authority-ledger source.
- Queue path, queue update time, and slice status counts.
- Four status lanes: internal agent work, external capability activity, validation/evidence, and supervision-required items.
- Worker leases, heartbeat state, stale handling, and reassignment or supersession notes.
- Blockers, dependencies, validation commands, and next actions for active slices.
- External-capability registry, result-packet, and dashboard connection artifact paths, omitting credentials and secret values.
- Incident/exception log path, any abnormal event summary, PHI-aware monitoring posture, and whether any incident entry needs user review.
- Accepted decision matrix path and whether any rows were added or superseded.
- Supervision queue status.
- Exact resume path and resume command or instruction.

## Supervision Queue

Maintain the living supervision queue at:

```text
_bmad-output/implementation-artifacts/supervision-queue.html
```

The queue must be well-formed semantic HTML with JSON-LD metadata and a table containing:

- Item ID.
- Date/time added or updated.
- Task requiring supervision.
- Reason supervision is required.
- Current state.
- Priority after the current safe task reaches a stopping point.
- Related slice or branch.
- Presentation status.

When the user announces their return, add any pending supervision-required items to this queue and present the queue once the current safe task completes or reaches a reasonable stopping point. Do not interrupt active safe work solely because the user returned unless the active task itself has reached a stop boundary or the user explicitly asks to stop.

## Run-State, Handoff Digest, And Incident-Log Renderer Requirement

Before the first real continuous run, implement and validate renderer commands that read `_bmad-output/implementation-artifacts/slice-queue.json` and write the required semantic HTML run-state artifact under `_bmad-output/implementation-artifacts/run-state/` plus handoff digest artifacts under `_bmad-output/implementation-artifacts/handoff-digest/`. Also implement and validate an incident-log renderer that reads `_bmad-output/implementation-artifacts/incident-log/incident-log.json` and writes semantic HTML incident/exception log artifacts under `_bmad-output/implementation-artifacts/incident-log/`.

The renderer is a production preflight requirement for autonomous runs. Manual run-state HTML is acceptable only for template design or emergency handoff, not for the first real continuous run.

The renderer must:

- Treat `slice-queue.json` as the source of truth for slice state and incident-log JSON as the append-only source of truth for incidents.
- Preserve the per-slice status table shape and allowed statuses.
- Include run metadata, branch/PR state, blockers, validation, worker summaries, and resume instructions when supplied by the coordinator.
- Include digest trigger, active authority, exact resume path, exact resume command or instruction, external-capability artifact paths, and supervision queue status when rendering a handoff digest.
- Include PHI-handling posture, HIPAA-aware variant status, monitoring phases, incident count, severity counts, and metadata-only safety notes when rendering an incident log.
- Produce well-formed local static HTML with JSON-LD metadata.
- Fail clearly when the queue is invalid, missing required fields, or contains an unknown status.
- Be covered by focused tests or a deterministic validation command before use in an unattended run.

## Per-Slice Status Table

Every run-state HTML artifact must include a per-slice table with one row per active, completed, blocked, waiting, merged, or superseded slice.

Every run-state HTML artifact should include a worker lease table when leases are present so a future coordinator can identify active, stale, reassigned, or superseded worker assignments without parsing worker reports first.

Allowed status values:

- `active`: work is currently in progress or ready to continue.
- `blocked`: the slice cannot proceed without a stop-condition resolution, external unblock, or user decision.
- `waiting-on-dependent`: the slice is ready but depends on another blocked or unvalidated slice.
- `validated`: the slice passed its associated tests and validation but has not yet been merged or superseded.
- `merged`: the slice was merged or otherwise integrated into the delivery branch.
- `superseded`: the slice was replaced by another slice, branch, or implementation path.

Use this table shape:

```html
<table>
  <thead>
    <tr>
      <th scope="col">Slice ID</th>
      <th scope="col">Status</th>
      <th scope="col">Branch</th>
      <th scope="col">Description</th>
      <th scope="col">Dependency/Blocker</th>
      <th scope="col">Validation</th>
      <th scope="col">Next action</th>
    </tr>
  </thead>
  <tbody>
    <tr class="status-active">
      <td>SLICE-001</td>
      <td><span class="status">active</span></td>
      <td><code>codex/YYYY-MM-DD-short-goal</code></td>
      <td>Brief contents</td>
      <td>None</td>
      <td>Focused test pending</td>
      <td>Continue implementation</td>
    </tr>
  </tbody>
</table>
```

The table is the primary resume surface for blocked-slice reporting. If slice A blocks and work moves to slice B, both slices must appear in the table.

## Machine-Readable Slice Queue

Maintain the machine-readable slice queue at:

```text
_bmad-output/implementation-artifacts/slice-queue.json
```

The coordinator owns this file. It is the source of truth for active slice state. Workers may propose slices, but the coordinator creates, updates, and assigns queue entries so ownership and dependencies stay coherent.

The queue must remain valid JSON and include:

- `schema_version`.
- `generated_by`.
- `updated_at`.
- `source_spec`.
- `source_of_truth`.
- `status_values`.
- `slices`.

Each slice entry must include:

- `id`.
- `status`.
- `branch`.
- `description`.
- `dependencies`.
- `blocked_by`.
- `proposed_by`.
- `assigned_to`.
- `worker_lease` when a worker owns or recently owned the slice, with `lease_id`, `worker_id`, `branch`, `assigned_paths`, `heartbeat_at`, `status`, `stale_after`, `stale_handling`, and `reassignment_or_supersession`.
- `allowed_paths`.
- `forbidden_paths`.
- `validation`.
- `next_action`.
- `updated_at`.

Allowed status values match the run-state table: `active`, `blocked`, `waiting-on-dependent`, `validated`, `merged`, and `superseded`.

Update the queue whenever a slice is created, assigned, leased, heartbeated, marked stale, reassigned, blocked, unblocked, validated, merged, superseded, or selected as the next active slice. A run-state HTML artifact should render this queue in human-readable table form instead of inventing divergent slice state.

## Resume Instruction Shape

Use this shape at the end of the run-state artifact:

```text
Resume from _bmad-output/specs/spec-agentic-wiki-continuous-codex-development/SPEC.md.
Read this run-state HTML artifact first.
Load _bmad-output/implementation-artifacts/slice-queue.json.
Continue from branch <branch> at commit <sha>.
Start with the first `active` slice in slice-queue.json, confirming it matches the HTML table.
Run <validation command> before the next commit.
Do not use computer-use or GUI-driving workflows without user supervision.
Load `_bmad-output/implementation-artifacts/supervision-queue.html` when the user announces their return and prioritize its actionable items at the next safe stopping point.
```
