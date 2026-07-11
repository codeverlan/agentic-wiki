# Plugin Project Boundary

## Purpose

Define the separation between the reusable agent-development plugin and the project-local memory that the plugin manages.

## Boundary Rule

The plugin is reusable machinery. A project is the mutable context. Project-specific shape, formation, guidance, decisions, run state, queues, and supervision needs belong in the target project's local memory artifacts, not in the plugin.

Project-local memory may customize the plugin's output, guidance, and behavior for that project through overlays applied after plugin defaults. This is the practical mechanism for project-specific plugin behavior without plugin mutation.

## Instruction And Overlay Precedence

When project-local overlays, BMAD artifacts, plugin defaults, repo instructions, and active user direction conflict, resolve them in this order:

1. System/developer/user safety and the active user request.
2. Repo-local `AGENTS.md` and explicit user project instructions.
3. BMAD artifacts, when present.
4. Project-local plugin overlay memory.
5. Plugin defaults.
6. Agent judgment.

Project overlays may specialize, narrow, or broaden plugin-guided agent behavior only inside the boundaries set by higher-priority instructions. They must surface conflicts instead of hiding or overriding them.

## Intent-To-Resource Scoping

The plugin should translate user intent and project memory into the resources a project needs, including agent skill sets, tool classes, worker shape, validation depth, escalation resources, and supervision-required actions.

This scoping is not a sandboxing ceremony. User-facing language should describe the resources needed for the project and the current task, not imply the user is primarily configuring restrictions.

## Primary Input Model

The plugin's primary input is a project-local intent packet. It is not limited to chat text, a single spec file, or a queue file. The plugin normalizes all accepted entry paths into the same project-local work contract before dispatching implementation.

Supported entry paths:

- Guided preflight spec: the plugin interviews the user enough to create a clear preflight spec or intent packet when no adequate artifact exists.
- Existing spec or resources: the user points the plugin at an existing BMAD spec, project spec, README, issue, ticket, architecture note, codebase, or other resource bundle.
- Hybrid intake: the user refers to partial resources and the plugin asks additional focused questions only for missing decisions that block a clear work picture.

The intent packet should capture:

- Objective and success signal.
- Target project root and authoritative sources.
- Existing specs/resources consumed or referenced.
- Intake depth: quick start, standard preflight, or deep spec.
- Whether the application will handle PHI, and if yes, the project-local HIPAA-aware incident-log and monitoring-phase paths.
- Active boundaries, stop conditions, and supervision-required actions.
- Project-local credential source requirements for external capabilities, including whether an env file, JSON file, process environment, or ephemeral chat credential is expected.
- Whether the user expects interactive check-ins or unattended continuous development.
- Validation baseline and high-risk review needs.
- Resource needs and missing-resource classification.
- Initial slice candidates, dependencies, and known blockers.
- Open questions that stop only affected slices unless they affect the run boundary or all remaining work.

The intent packet is project-local memory. It may be represented by BMAD artifacts when `_bmad` exists or by the plugin's lightweight Agentic Wiki memory when BMAD is absent. It must not be stored by mutating plugin files.

## Intent Packet Forms

Every intent packet has two synchronized project-local forms:

- JSON: the machine-readable dispatch input for preflight, queue derivation, worker assignment, validation planning, and resume.
- Semantic HTML: the human-facing review surface for the user, coordinator, and future agents.

The JSON form is authoritative for automation. The HTML form must render the same material decisions, open questions, resource needs, project archetype, design direction, validation baseline, and supervision boundaries in a readable format with JSON-LD metadata.

Suggested project-local template paths:

- `_bmad-output/implementation-artifacts/intent-packet/template.json`
- `_bmad-output/implementation-artifacts/intent-packet/template.html`
- `_bmad-output/implementation-artifacts/archetype-registry/template.json`
- `_bmad-output/implementation-artifacts/archetype-registry/template.html`

## Intake Depths

The plugin supports three intake depths:

- Quick start: minimum viable intent packet for small, clear, low-risk work.
- Standard preflight: default depth for implementation and continuous delivery; enough context to support safe unattended work and independent slice derivation.
- Deep spec: full BMAD-style product, UX, architecture, and implementation-readiness depth for large, risky, unclear, or high-dependency projects.

Unattended continuous development requires at least standard preflight for the primary orchestration profile. Secondary profiles in mixed projects require only essential fields unless they become high-risk, affect shared architecture, or define a major user-facing surface.

## Domain-Specific Intake

The plugin must do early project-scope classification before asking detailed questions. A WordPress website, web-based application, CLI/library, mobile app, data workflow, and clinical/regulated workspace each require different interview paths, resource scoping, risk checks, validation, and delivery slices.

The first intake pass should classify:

- Project archetype.
- Primary surface: website, application, service, library, workflow, content system, or mixed.
- Technical substrate and deployment surface when known.
- User-facing audience and primary user journeys.
- Content, data, account, commerce, authentication, or integration needs.
- Regulated, privacy, PHI, credential, publishing, or production boundaries.
- Visual/brand/design materiality.
- Existing resources that should replace or shorten interview questions.

When the archetype is unclear, the plugin asks a small number of discriminating questions rather than running a generic questionnaire.

The plugin should keep a small built-in archetype registry. The default registry is specified in `archetype-registry.md`; project-local memory may add or override registry guidance without mutating plugin files.

Mixed projects are first-class. When multiple archetypes apply, choose one primary orchestration profile and attach secondary profiles to scoped subprofiles or slices. Secondary profiles add targeted questions, validation, risk checks, and slice patterns without forcing the whole project through multiple full intake paths.

## Design-Assisted Intake

Projects with visual, frontend, content, brand, or user-experience concerns need explicit graphic-design agreement before implementation slices depend on visual direction.

The plugin may use image generation, installed design/UX skills, and local HTML mockups to suggest visual pathways during the interview or in the intent-packet HTML review form. These artifacts are decision aids, not final authority. The user selects, rejects, combines, or defers visual directions; selected visual decisions are recorded in project-local memory.

Design pathway suggestions should capture:

- Visual goal and audience.
- Brand constraints, existing assets, or inspiration sources.
- Design system or platform conventions when applicable.
- Color, typography, imagery, density, motion, accessibility, and responsive needs.
- Which generated images, mockups, or design-skill outputs were considered.
- Which direction was selected or intentionally deferred.

Generated images and design-skill outputs belong in project-local memory or artifact folders, not in plugin files.

When a scoped resource is unavailable, classify it before deciding whether to block:

- Expected resource unavailable: a skill, plugin, tool, browser capability, credential, route, or external resource should be available for the current slice but is not. Stop that slice, notify the user in chat, record the blocker, and continue independent slices when available.
- Future resource opportunity: the resource does not exist yet but would improve future work. Continue with available resources, record it in `_bmad-output/implementation-artifacts/future-resource-queue.html`, and note the opportunity for the user; if it is reusable plugin work, also record it as a plugin-improvement candidate.

Future-resource queue entries are advisory. They do not authorize new project work slices unless the user explicitly promotes them or an explicit plugin-development workflow promotes them. Keep this queue chronological and do not add priority or status triage fields.

## BMAD Awareness

The plugin should detect whether the target project has `_bmad` and select a project-local storage adapter.

- If `_bmad` exists, use the BMAD storage adapter and write project-local state into BMAD-compatible `_bmad-output` surfaces.
- If `_bmad` is absent, use the lightweight Agentic Wiki storage adapter and create a neutral project-local memory area with the same primitives.
- Do not require BMAD for non-BMAD projects.

## Storage Adapter Model

Storage adapters choose where project-local memory lives. They do not change the plugin's normalized work contract, instruction precedence, safety boundaries, or plugin immutability.

Required adapter primitives:

- Intent packet JSON and semantic HTML.
- Authority ledger JSON and semantic HTML.
- Accepted decision matrix JSON and semantic HTML.
- Worker report JSON and semantic HTML.
- Control index semantic HTML.
- Dashboard data contract JSON and semantic HTML.
- External-capability registry JSON and semantic HTML.
- External-capability result packet JSON and semantic HTML.
- Run-state HTML generated from the slice queue.
- Handoff digest HTML generated from the slice queue.
- Incident/exception log JSON and semantic HTML, with a HIPAA-aware metadata-only variant for PHI-handling projects.
- Branch ledger.
- Supervision queue.
- Future-resource queue.
- Decision log.
- Project plugin overlay.
- Plugin improvement queue.

The BMAD adapter maps these primitives to `_bmad-output` and BMAD-compatible artifacts when `_bmad` exists. The lightweight Agentic Wiki adapter maps the same primitives to a neutral project-local memory root when BMAD is absent.

## Plugin Skill Shape

The reusable plugin should be one plugin with multiple focused skills:

- Intake/preflight: normalizes user intent into the intent packet, asks whether the application handles PHI, selects storage adapter, intake depth, archetype profiles, resources, validation, monitoring phases, and supervision needs.
- Continuous-run coordinator: manages preflight, slice queue, workers, validation, blockers, repair attempts, branch ledger, run-state, handoff digests, incident logs, and continuous delivery.
- Status/report handoff: produces user-return summaries, status reports, run-state and resume surfaces, handoff digests, incident summaries, and supervision/resource queue presentations from project-local memory.
- Plugin-improvement review: reviews project-local plugin-improvement candidates when the user starts an explicit plugin-development workflow.

Shared schemas, templates, and scripts may live in the plugin. Project-specific outputs, decisions, queues, overlays, reports, and run state must be written through the selected storage adapter, not to plugin files.

## Skill-Level Mutation Permissions

Each plugin workflow skill has a bounded mutation surface:

- Intake/preflight may write intent packets, preflight readiness artifacts, initial queue/resource artifacts, resource-gap notes, supervision needs, and project-local overlays needed to make the work actionable. It must not edit project code.
- Continuous-run coordinator may edit assigned project code, docs, tests, fixtures, and coordinator-owned shared control artifacts when the active request and project boundaries allow implementation work.
- Status/report handoff is read-only against project code and planning artifacts, except for generating project-local reports, summaries, resume surfaces, and user-return presentations.
- Plugin-improvement review may write project-local candidate analysis and plugin-improvement queue updates. It must not modify plugin files, plugin cache files, marketplace entries, or global skill files unless the user explicitly starts plugin-development work.

When a planned write exceeds the selected skill's mutation permission, stop or reroute the affected slice instead of silently escalating the skill.

## Plugin Run Modes

Every intent packet must record one selected run mode:

- `interactive-intake`: guided conversation, resource review, and focused clarifying questions that produce or repair the intent packet and readiness artifacts.
- `unattended-continuous-run`: continuous development mode that may dispatch coordinator/worker slices and proceed through safe queue work; it requires standard preflight for the primary profile before dispatch.
- `status-only-report`: read/report mode for summaries, user-return presentations, queue review, and resume surfaces; it is read-only against project code and planning artifacts except for generated reports.
- `plugin-improvement-review`: review mode for reusable plugin-improvement candidates; it may update project-local candidate analysis and queues, but cannot modify plugin files unless the user explicitly starts plugin-development work.

The plugin may infer the run mode from user intent when the inference is safe and does not broaden mutation authority. If mode ambiguity changes allowed writes, worker dispatch, unattended behavior, or plugin mutation risk, record an enabling question or stop only the affected slice.

## Run Mode Transitions

Mode transitions must be classified before they change behavior:

- Narrowing transitions may happen autonomously when they reduce mutation authority, dispatch scope, or unattended execution. Examples include `unattended-continuous-run` to `status-only-report` when the user returns, asks for status, or a blocker report is due.
- Broadening transitions require clear authority before they expand mutation authority, dispatch scope, unattended behavior, or plugin-file risk.
- `status-only-report` may transition back to `unattended-continuous-run` only when the user expresses that intent or the project-local artifacts contain a recorded continuous-run directive that still applies.
- `interactive-intake` may transition to `unattended-continuous-run` only after standard preflight is satisfied for the primary profile.
- `plugin-improvement-review` may transition into plugin file changes only after the user explicitly starts plugin-development work.

If transition authority is missing or ambiguous, record an enabling question or stop only the affected slice while preserving independent safe work.

## Authority Ledger

Maintain a project-local authority ledger with synchronized JSON and semantic HTML forms. It records active user directives, inferred permissions, selected run modes, mode transitions, continuous-run directives, expiration or supersession notes, and the evidence used to authorize broadening transitions.

The ledger is not plugin memory and must be written through the selected storage adapter. Future agents use it before relying on chat history for broadening transitions, unattended work, or continuous-run directives.

The ledger source is append-only. Agents add new entries for new directives, inferred permissions, expirations, and supersessions. They do not rewrite prior source entries in place. Generated HTML or reporting views may derive current-active, superseded, or expired status from later entries to make review easier.

## Plugin Immutability

An agent-development project must not alter:

- The plugin installation.
- Plugin cache files.
- Marketplace entries.
- Global skill files.
- Plugin-level templates or defaults.
- Plugin-owned shared schemas, templates, or scripts during ordinary project work.

The plugin may copy, instantiate, or render templates into project-local memory. Once instantiated, those project-local artifacts are owned by the project, not by the plugin.

## Project Memory Ownership

Project-local guidance may include:

- Intent packets and preflight-spec outputs.
- Intent-packet JSON and semantic HTML forms.
- Storage adapter selection and memory root mapping.
- Project-archetype classification.
- Project-local archetype registry overlays.
- Design pathway options, generated images, mockups, and selected visual direction.
- Project-specific operating rules.
- Authority ledger entries for active directives, inferred permissions, run modes, transitions, continuous-run directives, expiration, and supersession.
- Accepted accelerated-interview decisions in a project-local matrix, including source confirmation, implementation surface, accepted or superseded status, and later correction links when applicable.
- Slice-local worker reports, including assigned paths, lease/heartbeat state, changes made, validation run, blockers, proposed shared-control-artifact changes, and authority or resource evidence.
- Control index navigation surface.
- Incident/exception logs, including PHI-handling answer, HIPAA-aware metadata-only variant status, monitoring phases, incident counts, severity counts, and evidence pointers without PHI or secret values.
- Dashboard data snapshots and dashboard handoff artifacts.
- External-capability result packets and dashboard-visible external connection lists.
- External-capability registry entries for project-scoped plugins, skills, APIs, MCP servers, browser tools, external services, and spawned agents.
- Credential-source metadata for project-scoped external capabilities; record local file path, auth scheme label, rotation note, and local-only policy when useful, but never record secret values.
- Slice queue entries, including worker leases, heartbeat timestamps, stale handling, and reassignment or supersession notes when workers own or recently owned slices.
- Branch ledgers.
- Supervision queues.
- Future-resource opportunity queues.
- Run-state HTML.
- Handoff digest HTML.
- Incident/exception log HTML.
- Decision logs.
- Accepted decision matrices.
- BMAD-derived guidance.
- Plugin-derived project setup choices.
- Project-specific output and guidance overlays.
- Plugin improvement candidates that are not yet approved plugin changes.

These artifacts must live inside the target project or its configured memory workspace.

Required project-local HTML artifacts:

- `_bmad-output/implementation-artifacts/intent-packet/*.html`: human-facing intent packet review surfaces.
- `_bmad-output/implementation-artifacts/authority-ledger/*.html`: human-facing authority ledger review surfaces for active directives, inferred permissions, run modes, transition authority, and expiration or supersession notes.
- `_bmad-output/implementation-artifacts/worker-reports/*.html`: human-facing slice-local worker reports used by the coordinator before accepting or rejecting worker output, including lease heartbeat evidence.
- `_bmad-output/implementation-artifacts/control-index.html`: navigation-only index of project-local agent-development artifacts and categorized HTML files.
- `_bmad-output/implementation-artifacts/handoff-digest/*.html`: human-facing stop, compaction-risk, stale-lease, and user-return digests.
- `_bmad-output/implementation-artifacts/incident-log/*.html`: human-facing metadata-only incident/exception logs with PHI-aware monitoring phases when applicable.
- `_bmad-output/implementation-artifacts/dashboard-data/*.html`: human-facing review surfaces for dashboard data contracts consumed by visualization plugins or tools.
- `_bmad-output/implementation-artifacts/external-capabilities/*.html`: project-local external capability registry and handoff review surfaces.
- `_bmad-output/implementation-artifacts/archetype-registry/*.html`: project-local archetype registry and override review surfaces.
- `_bmad-output/implementation-artifacts/project-plugin-overlay.html`: project-specific rules, guidance adjustments, output preferences, and memory-derived behavior overlays applied after plugin defaults.
- `_bmad-output/implementation-artifacts/plugin-improvement-queue.html`: reusable plugin improvement candidates discovered during project work.
- `_bmad-output/implementation-artifacts/future-resource-queue.html`: simple chronological advisory queue of useful resources that do not exist yet and should be considered later without blocking current work.

## Overlay Semantics

Project overlays may:

- Add project-specific instructions for how plugin-generated queues, reports, handoffs, and guidance should be shaped.
- Add project-archetype-specific interview prompts and design pathway presentation rules.
- Narrow plugin behavior for a project when the project has stricter safety, workflow, documentation, or validation rules.
- Broaden agent autonomy or enable additional agent skill sets when higher-priority boundaries already authorize that autonomy.
- Guide how the plugin translates user intent into project-specific resources, validation, escalation, and supervision needs.
- Add project-local prompts, labels, report sections, validation expectations, or presentation rules.
- Record memory-derived preferences that should affect future runs in the same project.
- Suggest natural user check-in points at major milestones while preserving unattended continuous-development priority when the user has asked to be away.
- Shape which external plugins, skills, APIs, MCP servers, browser tools, external services, or spawned agents are appropriate for this project, as long as higher-priority boundaries permit their use.
- Select project-local credential sources for those external capabilities without mutating plugin files or requiring a standardized credential payload.
- Specialize incident-log monitoring phases and HIPAA-aware development safeguards for a PHI-handling project without mutating the reusable plugin.

Project overlays must not:

- Change plugin source files.
- Change plugin marketplace metadata.
- Change global skill instructions.
- Change plugin defaults for other projects.
- Override or hide conflicts with higher-priority instructions, safety boundaries, or the active user request.

## Improvement Queue

When a project discovers a potentially reusable plugin improvement, record it in `_bmad-output/implementation-artifacts/plugin-improvement-queue.html` with:

- Candidate ID.
- Date/time updated.
- Source project.
- Proposed reusable improvement.
- Evidence from project work.
- Affected workflow or plugin surface.
- Priority.
- Status.
- Whether an explicit plugin-development workflow has been started.

Plugin improvement candidates are proposals. They do not authorize plugin mutation.

## Plugin Change Requests

When a project reveals a reusable plugin improvement, record the proposal in project-local memory as a plugin-improvement candidate. Do not modify the plugin unless the user explicitly starts a plugin-development workflow.

## Success Criteria

- Running the plugin in two different projects produces separate project-local memory surfaces.
- A user can begin from a guided preflight interview, an existing spec/resource bundle, or a hybrid of resources plus clarifying questions and end with the same actionable project-local intent packet shape.
- The same plugin asks different relevant questions for a WordPress website than for a web application, while still producing the same normalized intent-packet structure.
- Visual projects capture graphic-design agreement through project-local design pathway evidence before implementation depends on visual direction.
- Project-specific rules from project A do not alter plugin behavior for project B.
- Project A can still customize plugin output and guidance through project-local overlays without changing the plugin.
- The plugin can be upgraded independently of project-local memory.
- A future plugin-development workflow can review project-local improvement candidates without treating them as already-approved plugin changes.
- PHI-handling projects can enable HIPAA-aware incident monitoring through project-local memory while non-PHI projects keep the generic incident-log shape.
