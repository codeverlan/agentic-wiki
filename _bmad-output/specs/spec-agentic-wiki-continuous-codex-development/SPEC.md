---
id: SPEC-agentic-wiki-continuous-codex-development
companions:
  - ../../../README.md
  - ../../../AGENTS.md
  - plugin-project-boundary.md
  - agent-topology.md
  - task-graph.md
  - tdd-contract.md
  - blocking-policy.md
  - validation-contract.md
  - handoff-runbook.md
  - delivery-workflow.md
  - archetype-registry.md
sources:
  - ../../../docs/architecture.html
  - ../../../docs/operations.html
  - ../../../docs/hipaa-local.html
  - ../../../src/memwiki/api.py
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability only; consult them only if a future change needs narrative rationale or source prose this contract intentionally omits.

# Agentic Wiki Continuous Codex Development

## Why

Agentic Wiki needs a durable handoff contract so future Codex sessions can keep improving a brownfield Python CLI/library without relying on chat history. The work matters because Agentic Wiki carries strict memory, provenance, compatibility, documentation, and clinical local-only constraints; unattended or parallel development must preserve those constraints while still making safe progress.

## Capabilities

- **CAP-1**
  - **intent:** Future Codex threads can load one spec bundle and identify the repo rules, source surfaces, validation gates, and first safe task.
  - **success:** A new thread can start from `handoff-runbook.md`, cite the active work queue, and name required validation without asking for prior chat context.
- **CAP-2**
  - **intent:** Codex can decompose approved work into independent TDD slices that parallel agents can execute without colliding.
  - **success:** A coordinator can assign non-overlapping file ownership and expected tests for at least two simultaneous slices from `task-graph.md`.
- **CAP-3**
  - **intent:** Codex can continue useful low-risk work when one task blocks without inventing decisions or crossing project safety boundaries.
  - **success:** A blocked primary task produces a blocker note and a fallback task chosen from `blocking-policy.md` without editing protected surfaces.
- **CAP-4**
  - **intent:** Codex can preserve the canonical `agentic_wiki` public surface and the legacy `memwiki` compatibility surface through feature work.
  - **success:** Changes affecting API or CLI behavior update canonical and compatibility tests, keep CLI wrappers thin, and preserve machine-readable JSON outputs.
- **CAP-5**
  - **intent:** Codex can keep generated documentation, capability manifests, schemas, workflows, storage layout, and validation behavior aligned with code changes.
  - **success:** Any command, schema, workflow, storage, or validation change includes docs check evidence and updates generated docs or capability metadata as needed.
- **CAP-6**
  - **intent:** Codex can enforce clinical PHI local-only constraints during implementation and validation.
  - **success:** PHI-reading or mutating operations require `OperationContext`, tests use synthetic data only, and static export remains blocked unless explicitly deidentified or synthetic.
- **CAP-7**
  - **intent:** The future agent-development plugin can support BMAD projects without depending on BMAD or mutating itself for project-specific needs.
  - **success:** Projects with `_bmad` use the BMAD storage adapter, projects without `_bmad` use the lightweight Agentic Wiki storage adapter, both adapters expose the same project-local primitives, and all project-specific shape/guidance/overlays are written to project-local memory rather than plugin files.
- **CAP-8**
  - **intent:** The future agent-development plugin can turn user intent, existing specs, and project resources into an actionable project-local work contract.
  - **success:** The plugin routes through explicit run modes and focused skills for intake/preflight, continuous-run coordination, status/report handoff, and plugin-improvement review; creates or updates a project-local intent packet from a quick-start intake, standard preflight, deep spec, referred specs/resources, or a hybrid of resources plus clarifying questions; then uses it to derive readiness checks, slice queues, check-in points, and continuous-run priorities.
- **CAP-9**
  - **intent:** The future agent-development plugin can adapt intake questions, design prompts, and validation planning to the type of project being started or continued.
  - **success:** The plugin classifies project archetype through the built-in registry in `archetype-registry.md`, uses primary and secondary profiles for mixed projects, asks different scope questions for materially different project types such as WordPress websites and web applications, records graphic-design decisions when visual work is material, and uses image generation or installed design/UX skills to offer user-selectable visual pathways.
- **CAP-10**
  - **intent:** Parallel workers can hand evidence back to the coordinator without mutating shared control artifacts directly.
  - **success:** Each worker produces a slice-local JSON and semantic HTML worker report before coordinator acceptance or rejection, including assigned paths, lease/heartbeat state, changes made, validation run, blockers, proposed shared-control-artifact changes, and authority or resource evidence.
- **CAP-11**
  - **intent:** Users and future agents can find the project-local control surfaces without treating a dashboard as a new source of truth.
  - **success:** The plugin maintains a project-local semantic HTML control index that links the current intent packet, authority ledger, slice queue, run-state, handoff digest, branch ledger, supervision queue, future-resource queue, plugin-improvement queue, worker reports, dashboard data contract, external-capability registry, external-capability result packets, status-lane summaries, and categorized HTML artifacts.
- **CAP-12**
  - **intent:** The future plugin can coordinate external capabilities without having to own every visualization, research, API, MCP, browser, or agent function itself.
  - **success:** The plugin records external capability needs, invocations, results, and connection presence in project-local JSON/HTML artifacts; can call or delegate to available plugins, skills, APIs, MCP servers, browser tools, and spawned agents inside active authority and supervision boundaries; can reference project-local env or JSON credential sources without copying secret values or standardizing payload shape; and can hand bounded project-status data including visible external connection lists to visualization plugins such as `build-web-data-visualization` when a rich dashboard is requested.
- **CAP-13**
  - **intent:** The future plugin can preserve abnormal coordinator events, exception handling, and PHI-aware security monitoring without storing sensitive values.
  - **success:** Each project has an append-only project-local incident/exception log with JSON and semantic HTML forms; the intake flow expressly asks whether the application will handle PHI; PHI-handling projects use a HIPAA-aware metadata-only log variant with monitoring phases for regular security scans and abnormal-behavior review; and incident evidence never stores PHI, credentials, tokens, passwords, secret values, or raw sensitive text.
- **CAP-14**
  - **intent:** The future plugin can accelerate elicitation by presenting inferred question-and-answer decision matrices and implementing user-confirmed rows without repeating settled interview work.
  - **success:** Accepted accelerated-interview decisions are stored in project-local JSON and semantic HTML matrix artifacts, future agents consult that matrix before reopening questions, corrections use project-local update or supersession records, and reusable plugin defaults change only through an explicit plugin-development workflow.

## Constraints

- Use tests before behavior changes.
- Keep canonical memory pages as semantic HTML with JSON-LD metadata.
- Never write generated knowledge directly into canonical `wiki/` output; use `drafts/<draft-id>/`.
- Preserve claim-level provenance for every substantive generated claim.
- Keep raw source files immutable after ingest.
- Run `uv run pytest`, `uv run ruff check .`, `uv run mypy src`, and affected docs checks before claiming completion.
- Treat `AgenticWikiWorkspace` from `agentic_wiki` as the canonical public integration surface.
- Keep `memwiki.MemwikiWorkspace` and the `memwiki` CLI as compatibility shims unless removal is explicitly approved.
- Keep CLI commands as thin wrappers around the public API.
- Preserve machine-readable JSON outputs for agent-facing commands.
- Keep `.memwiki/agent-capabilities.json` aligned with supported commands and mutation policy.
- For clinical PHI workspaces, require `OperationContext` on PHI-reading or mutating operations.
- Do not use actual PHI in tests, examples, prompts, screenshots, logs, or documentation.
- Keep clinical PHI workspaces strict local-only: no remote model adapters, cloud PHI storage, telemetry, or static PHI export unless explicitly deidentified or synthetic.
- Preserve dirty-tree boundaries and never overwrite user-authored artifacts silently.
- Start every continuous run with coordinator-owned dirty-worktree intake before dispatching workers: classify existing changes by path as active slice, prior-agent work, user/unowned work, or generated artifact; preserve unowned changes; stop planned slices that overlap unclear dirty paths while continuing independent slices.
- Start every continuous run with a coordinator-owned preflight readiness gate before dispatching workers: verify the spec/handoff are loaded, slice queue parses, dirty-worktree intake is complete, branch ledger is current, run-state and handoff digest renderers are available, validation baseline is selected, resource/credential-source/supervision needs are identified, and at least one safe slice is ready.
- Future Codex sessions have continuous-delivery autonomy by default: they may use full unsandboxed filesystem, shell, git, network, branch, commit, push, PR, and fallback-task execution within this project unless the active user request narrows scope.
- Future Codex sessions may use one coordinator plus up to six simultaneous workers by default.
- Use BMAD artifacts as the authoritative work source; use agent-derived safe fallback tasks only when BMAD work is empty, blocked, or silent on the next safe action.
- Keep parallel slices identified throughout continuous runs; a blocker or validation failure in one slice must not stop project development when an independent slice can proceed.
- Only the coordinator may mutate shared control artifacts including `slice-queue.json`, branch ledger, run-state artifacts, handoff digest artifacts, supervision queue, future-resource queue, plugin-improvement queue, and project overlay files.
- Workers modify only assigned code, docs, tests, fixtures, their own slice-local worker report, or approved slice-local artifacts; proposed control-artifact changes must be submitted in worker reports for coordinator acceptance after validation.
- Active worker assignments use project-local leases. Each active slice records worker ID, branch, assigned paths, lease or heartbeat timestamp, and stale-worker handling; if a worker stalls or disappears, the coordinator marks the lease stale, inspects and preserves slice-owned work, then reassigns or supersedes the slice without blocking independent slices.
- Worker reports must be slice-local project memory with synchronized JSON and semantic HTML forms. Before the coordinator accepts or rejects worker output, the worker report must record assigned paths, changes made, validation run, blockers, proposed shared-control-artifact changes, and authority or resource evidence.
- Maintain `_bmad-output/implementation-artifacts/control-index.html` as a semantic HTML navigation surface for project-local agent-development artifacts. It is not a source of truth; it links and categorizes authoritative artifacts.
- Maintain a dashboard data contract under `_bmad-output/implementation-artifacts/dashboard-data/` that future visualization plugins can consume for status, agent activity, pending/active/completed tasks, token use when available, validation state, blockers, queues, external connections, external capability result summaries, and categorized HTML files.
- Dashboard status data must separate project activity into four lanes: internal agent work, external capability activity, validation/evidence, and supervision-required items. Each lane records current, blocked, completed, and last-updated summaries while preserving a combined top-level project status.
- Rich interactive dashboards are optional project-local generated artifacts. They may be produced by external plugins or tools, such as `build-web-data-visualization` or Data Analytics widgets, from bounded snapshots of project-local memory; the reusable agent-development plugin should coordinate that handoff rather than hard-coding every visualization model.
- Programmed dashboards and dashboard data contracts must include a visible list of external connections used or planned for the project, including APIs, MCP servers, installed plugins, skills, browser tools, external services, local tools, and spawned agents. Credentials and secret values are never displayed.
- Maintain an external-capability registry under `_bmad-output/implementation-artifacts/external-capabilities/` for project-scoped plugins, skills, APIs, MCP servers, browser tools, external services, and spawned agents. Record capability type, purpose, availability, authority, autonomy boundary, supervision or credential-source metadata, evidence capture, fallback, and related slices.
- Maintain external-capability result packets under `_bmad-output/implementation-artifacts/external-capability-results/`. Each external invocation must be represented either in a slice-local worker report or in a dedicated JSON/HTML result packet with replay/audit metadata, bounded input/output summaries, provenance, and links back to the capability registry without storing secrets, credential values, hidden reasoning, or oversized payloads.
- Once the coordinator accepts an external-capability result packet, that packet is immutable; corrections create a superseding result packet that links to the prior packet. Dashboards display the current accepted packet while preserving history links to superseded packets.
- When validation fails after an implementation slice, allow two bounded self-repair attempts inside that slice; after the second unsuccessful repair, stop the slice, record failure evidence, preserve or revert only slice-owned changes as appropriate, and continue independent slices.
- Never roll back unrelated user changes or other slices as part of validation-failure repair.
- Workers may propose new independent slices, but the autonomous coordinator creates and assigns slices; user approval is not required for new safe slices unless a stop condition is crossed.
- Stop continuous runs first at any explicit time, token, work, or platform usage limit; otherwise stop when the BMAD queue and safe fallback queue are drained and associated tests pass.
- Agents may make reversible implementation-level decisions autonomously during a run when the choice stays inside current requirements and safety boundaries; record the decision and rationale in run evidence.
- Run evidence must distinguish `implementation`, `architecture`, and `product` decisions so reversible local choices are separated from higher-authority choices.
- Unresolved architecture or product decisions stop only the affected slice and dependent slices while independent slices continue; pause the whole run only when the decision affects the run boundary, shared architecture, safety constraints, or all remaining work.
- When a run stops before queue drain, write a durable resume artifact under `_bmad-output/implementation-artifacts/`.
- Use well-formed semantic HTML for human-facing continuous-run artifacts wherever BMAD does not require Markdown.
- Run-state artifacts must be HTML files with a per-slice table using the statuses `active`, `blocked`, `waiting-on-dependent`, `validated`, `merged`, and `superseded`.
- Maintain `_bmad-output/implementation-artifacts/slice-queue.json` as the machine-readable source of truth for active slice state; HTML run-state artifacts summarize that JSON for humans.
- Maintain project-local semantic HTML handoff digests under `_bmad-output/implementation-artifacts/handoff-digest/`. Generate a digest at every stop, compaction-risk point, stale lease, or user-return checkpoint; each digest must summarize objective, active authority, queue state, worker leases, blockers, validation, external capabilities, supervision items, and the exact resume command or path.
- Maintain project-local append-only incident/exception logs under `_bmad-output/implementation-artifacts/incident-log/` with synchronized JSON and semantic HTML forms. Record abnormal coordinator events, expected-but-unavailable resources, exhausted repair loops, stale leases, safety or supervision stops, whole-run stops, suspicious behavior, credential/secret exposure risk, and PHI monitoring alerts as metadata-only evidence.
- If the software being developed will handle PHI, use the HIPAA-aware incident-log variant: the intake interview must ask whether the application handles PHI; the log must declare that it stores no PHI or secret values; monitoring phases must remind the user to run regular dependency, secret, static-analysis, and access-review scans; and monitoring must include abnormal-behavior review for possible leakage of information, credentials, plans, or PHI.
- HIPAA-aware incident logs support software-development best practices such as least privilege, audit logging, access review, encryption planning, backup planning, incident response, and deidentification discipline, but they do not certify HIPAA compliance or replace organizational risk analysis, legal review, workforce policy, BAAs, or production compliance operations.
- Maintain `_bmad-output/implementation-artifacts/accepted-decisions/decision-matrix.json` and `_bmad-output/implementation-artifacts/accepted-decisions/decision-matrix.html` as the project-local source for user-accepted accelerated interview decisions. Future agents must consult the matrix before reopening settled questions. Accepted rows are not plugin defaults until an explicit plugin-development workflow promotes them.
- During unattended runs, routine progress must update local artifacts rather than chat: `slice-queue.json`, run-state HTML, handoff digest HTML when a digest trigger occurs, branch ledger, and memlog are the default status surfaces.
- Chat notifications are reserved for expected-but-unavailable resources, exhausted repair loops, supervision-required tasks, whole-run stops, safety/privacy boundaries, and user-return queue presentation; a chat summary must be available on request.
- If the user chooses to pass a credential in chat for development work, agents may use it for the approved task while making best efforts not to store, echo, log, commit, or persist the credential value in durable artifacts.
- Assume ordinary credential use is for development unless the user explicitly says otherwise; development credentials, policies, and passwords are expected to differ from production.
- Before the first real continuous run, implement and validate renderer commands that derive run-state and handoff digest HTML from `slice-queue.json` and incident-log HTML from incident-log JSON; production continuous runs must not rely on manually edited run-state, handoff digest, or incident-log HTML.
- Pull requests are non-blocking audit and synchronization artifacts; do not treat user approval, PR opening, or traditional PR review as the gate for continuing development.
- Use `codex/<date>-<short-goal>` for user-facing branches and `codex/auto/<date>-<queue-slice>` for unattended queue-slice branches.
- Keep `_bmad-output/implementation-artifacts/branch-ledger.html` as the living HTML branch ledger with branch name, date/time last updated, and brief contents description.
- Agents must follow the mandatory problem-solving escalation ladder before treating ordinary difficulty as blocked: local repo/tests/logs/docs/BMAD/Agentic Wiki memory; package/tool output and official project docs; web search or current official ecosystem docs; installed skills/plugins and scoped browser verification; parallel or adversarial worker review; then slice splitting, independent-slice continuation, or supervision-queue entry.
- Non-local sources used to justify implementation, blockers, or architecture/product decisions must be recorded as evidence metadata: source URL or tool/plugin name, retrieval or check time, supported claim, and whether the source was official or current enough.
- Do not copy large external source content into run evidence; preserve concise metadata and the decision it supported.
- High-risk slices require review-worker or adversarial review before merge or integration when they touch PHI/privacy, credentials, public API compatibility, schema/storage layout, plugin-core behavior, generated docs contracts, security boundaries, or deployment/publishing behavior.
- Ordinary low-risk implementation slices may rely on tests plus coordinator review.
- Scoped browser or Chrome-control plugins may be used autonomously for development verification, rendered-route inspection, docs research, screenshots, accessibility checks, and local app validation.
- Escalation ladder steps may be skipped only when a time, budget, safety, permission, privacy, or task-applicability boundary prevents them; skipped steps and reasons must be recorded in blocker notes or run-state evidence.
- Broad computer-use or human-desktop control still requires supervision, as do personal-account login, external production changes, sensitive pages, purchases, publishing, messaging, and irreversible account actions.
- Maintain `_bmad-output/implementation-artifacts/supervision-queue.html` as the living HTML list of tasks requiring user supervision; user return appends or prioritizes that list at the next reasonable stopping point without necessarily interrupting active safe work.
- The future plugin must be BMAD-aware but not BMAD-dependent: use BMAD artifacts when `_bmad` exists, and otherwise create/use a lightweight project-local agent-development memory with equivalent primitives.
- The future plugin must use a storage adapter model: BMAD adapter when `_bmad` exists, lightweight Agentic Wiki adapter otherwise, with both adapters exposing equivalent primitives for intent packet, authority ledger, worker reports, control index, dashboard data contract, external-capability registry, external-capability result packets, run-state HTML, handoff digest HTML, incident/exception log JSON and HTML, branch ledger, supervision queue, future-resource queue, decision log, project plugin overlay, and plugin improvement queue.
- The future plugin should be organized as multiple focused skills under one plugin: intake/preflight, continuous-run coordinator, status/report handoff, and plugin-improvement review. Shared schemas, templates, and scripts are plugin-owned; project-specific outputs are written through the selected storage adapter rather than plugin files.
- Plugin workflow skills must enforce skill-level mutation permissions: intake/preflight writes only intent, readiness, queue, and resource artifacts; continuous-run coordinator may edit code, docs, tests, and shared control artifacts; status/report handoff is read-only except generated reports; plugin-improvement review may write candidate analysis but cannot modify plugin files unless the user explicitly starts plugin-development work.
- Intent packets must record the selected plugin run mode: `interactive-intake`, `unattended-continuous-run`, `status-only-report`, or `plugin-improvement-review`. The plugin may infer mode from user intent when safe; unattended continuous runs require standard preflight, status-only mode remains read/report-only, and plugin-improvement review cannot mutate plugin files unless the user explicitly starts plugin-development work.
- Mode transitions must classify whether they narrow or broaden authority. Narrowing transitions may happen autonomously, such as `unattended-continuous-run` to `status-only-report` on user return or blocker reporting. Broadening transitions require clear authority: `status-only-report` back to `unattended-continuous-run` requires user intent or a recorded continuous-run directive, `interactive-intake` to `unattended-continuous-run` requires standard preflight, and `plugin-improvement-review` to plugin file changes requires explicit plugin-development work.
- Maintain a project-local authority ledger in synchronized JSON and semantic HTML forms. The ledger records active user directives, inferred permissions, selected run modes, mode transitions, continuous-run directives, expiration or supersession notes, and authority evidence for broadening transitions so future agents do not rely on stale chat assumptions.
- Authority-ledger source entries are append-only: agents add new entries for new directives, inferred permissions, expirations, and supersessions. Prior source entries are not rewritten in place; generated views may derive and display current-active, superseded, or expired status from later entries.
- The future plugin's primary input is a project-local intent packet normalized from guided preflight-spec interview answers, existing specs or resources referred by the user, or a hybrid of resources plus clarifying questions.
- The future plugin must support quick start, standard preflight, and deep spec intake depths; unattended continuous development requires at least standard preflight for the primary profile, while secondary profiles need essential fields unless they become high-risk.
- Intent packets must have a machine-readable JSON form for dispatch and a human-facing semantic HTML form for user review.
- The plugin may ask additional focused questions when the intent packet lacks the information needed to form a clear actionable work picture.
- The plugin must classify project archetype during early intake and adapt questions, resources, validation, and slice planning to that archetype.
- The future plugin should include a small built-in archetype registry with project-local override support; project-local overrides may add or refine guidance without mutating plugin files.
- Mixed projects must select one primary orchestration profile and attach secondary archetypes as scoped profiles or slices; profile conflicts stop only affected slices unless they cross a whole-project or higher-risk shared boundary.
- For visual or frontend work, the plugin must capture graphic-design agreement and may use image generation plus installed design/UX skills to suggest visual pathways during interview or HTML intent-packet creation.
- The plugin may suggest natural user check-in points at major milestones, but if the user indicates long unattended continuous development, check-ins are advisory unless a stop, safety, or supervision boundary applies.
- Agent-development projects must not mutate the reusable plugin to encode project-specific shape, formation, guidance, decisions, queues, or run state.
- Project-level shape, formation, and guidance produced by the plugin or BMAD must live in that project's local Agentic Wiki/BMAD memory artifacts, not in the plugin installation, plugin cache, marketplace entry, or global skill files.
- Projects may customize plugin outputs, guidance, and behavior through project-local memory overlays that are applied after plugin defaults; this creates project-specific plugin behavior without mutating the reusable plugin.
- Resolve instruction and guidance conflicts in this order: system/developer/user safety and active request; repo-local `AGENTS.md` and explicit user project instructions; BMAD artifacts when present; project-local plugin overlay memory; plugin defaults; agent judgment.
- Project-local overlays and plugin defaults must not override higher-priority instructions, safety boundaries, or the active user request.
- Project-local overlays may narrow, specialize, or broaden agent autonomy inside higher-priority boundaries so each project can enable the agent skill sets it needs.
- The plugin should translate user intent into project-local resource scoping, skill needs, validation needs, and supervision needs without presenting the work as sandboxing or an autonomy profile.
- The plugin's coordinator must be able to call, route to, or spawn work through external capabilities when the project needs them, including installed plugins, skills, APIs, MCP servers, browser tools, external services, and subagents. External capability use is autonomous when it stays inside active project authority, dev/local boundaries, and existing supervision rules; credentials, production changes, publishing, personal-account actions, broad computer-use, sensitive data access, or unclear authorization require the applicable explicit direction or supervision.
- External capability credentials must live in project-local env files, JSON files, process environment, or approved ephemeral chat use; the reusable plugin must never embed credential values or require one standardized credential schema because API keys, usernames/passwords, tokens, browser sessions, and service-specific auth payloads vary by project and provider.
- Durable artifacts may record credential source metadata such as local path, capability, auth scheme label, rotation note, and whether the source is local-only, but must not store, echo, log, commit, or render secret values.
- If resource scoping identifies an expected skill, plugin, browser capability, tool, or external resource that should be available but is not, stop only the affected slice, notify the user in chat, and record the blocker in run-state evidence.
- If resource scoping identifies a useful resource that does not exist yet, continue with available resources and note the future-resource opportunity to the user instead of blocking the slice.
- Maintain `_bmad-output/implementation-artifacts/future-resource-queue.html` as the living HTML queue of useful resources that do not exist yet and should be considered later.
- Future-resource queue entries are advisory only and must not become project work slices unless the user explicitly promotes them or an explicit plugin-development workflow promotes them.
- Keep the future-resource queue as a simple chronological advisory queue; do not add priority or status triage fields.
- Maintain project-local HTML artifacts for plugin-adjacent project state: `_bmad-output/implementation-artifacts/project-plugin-overlay.html` for project-specific overlays and `_bmad-output/implementation-artifacts/plugin-improvement-queue.html` for reusable plugin improvement candidates.
- Changes to the plugin itself require an explicit plugin-development workflow and must not be inferred from ordinary project-level continuous development work.
- Do not engage computer-use or GUI-driving workflows without user supervision.

## Non-goals

- Do not use unsupervised computer-use, GUI-driving, or human-desktop-control workflows.
- Do not replace the BMAD lifecycle artifacts with this kernel; use it as the contract for downstream PRD, architecture, stories, and implementation.
- Do not mutate the reusable plugin or plugin marketplace as a side effect of adapting to one project.
- Do not remove legacy `memwiki` surfaces unless the user explicitly approves that migration.
- Do not create, ingest, or demonstrate with real PHI.

## Success signal

A fresh Codex thread can load this spec folder, normalize user intent or referred resources into an actionable project-local intent packet, pass the preflight readiness gate, select a safe next Agentic Wiki task, keep independent parallel slices available, use worker leases to detect stale assignments, implement with tests first, collect slice-local worker reports before coordinator disposition, expose project-local status and external connection presence through the control index and optional dashboard data contract with four status lanes, generate handoff digests at stops, compaction-risk points, stale leases, and user-return checkpoints, route around blocked slices, coordinate needed external capabilities, capture external invocations in worker reports or result packets, stop at the configured time/budget or queue-drain boundary, and close with exact validation evidence while preserving Agentic Wiki's HTML, provenance, compatibility, documentation, and PHI-local contracts.

For PHI-relevant projects, that same thread can answer from durable artifacts whether the application handles PHI, locate the HIPAA-aware metadata-only incident log, see monitoring phases for regular security scans and abnormal-behavior review, and confirm that incident evidence stores pointers and summaries rather than PHI, credentials, or raw secret values.

For accelerated intake, that same thread can locate the accepted decision matrix, identify which questions already have user-approved answers, and continue implementation or plugin packaging without re-interviewing settled decisions unless a project-local correction or supersession exists.

## Assumptions

- The target software is the current Agentic Wiki repo at `/Users/tyler-lcsw/.codex/worktrees/6999/memory_system`.
- Default BMAD artifact paths are acceptable because the intake was confirmed.
