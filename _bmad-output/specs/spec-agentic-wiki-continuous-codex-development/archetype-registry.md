# Archetype Registry

## Purpose

Define the future plugin's default project archetypes so early intake can choose relevant interview questions, resource scoping, validation, risk review, and slice patterns without using a generic questionnaire.

## Registry Rule

The reusable plugin owns the built-in default registry. Projects may add, narrow, or override archetype guidance through project-local memory overlays and intent-packet fields. Project overrides must not mutate plugin files.

Each archetype entry defines:

- Discriminating questions.
- Likely design needs.
- Validation baseline.
- High-risk surfaces.
- Common slice patterns.

## Mixed-Project Policy

When multiple archetypes apply, select one primary archetype for orchestration and attach secondary archetypes as scoped subprofiles or slices. The primary profile controls the top-level intake path, queue shape, and milestone framing. Secondary profiles add their own discriminating questions, validation baselines, high-risk surfaces, and slice patterns only to the affected project areas.

Conflicts between archetype profiles stop only the affected slice and dependent slices unless the conflict changes the whole project boundary, shared architecture, safety boundary, plugin-core behavior, PHI/privacy boundary, public API compatibility, or all remaining work.

## Default Archetypes

### WordPress/content website

- Discriminating questions: content model, page templates, block/theme strategy, plugin inventory, editorial workflow, hosting, SEO, redirects, forms, analytics, accessibility, performance budget, deployment target.
- Likely design needs: brand expression, typography, imagery, content hierarchy, reusable blocks, responsive page layouts, accessibility contrast, editorial component consistency.
- Validation baseline: rendered page checks, link/redirect checks, block/theme smoke tests, accessibility scan, performance scan where available, backup/deploy verification for live sidecars.
- High-risk surfaces: production publishing, credentials, personal-account login, commerce, forms collecting sensitive data, SEO-critical redirects, live content deletion.
- Common slice patterns: content inventory, theme/block audit, design direction, template implementation, form integration, SEO/redirect pass, accessibility/performance pass, deployment verification.

### Web application/SaaS

- Discriminating questions: user roles, core workflows, data model, authentication, authorization, API contracts, state management, environments, billing, observability, acceptance criteria, deployment target.
- Likely design needs: app information architecture, task density, component system, dashboard/table patterns, form states, empty/error/loading states, responsive behavior, accessibility floor.
- Validation baseline: unit/integration tests, API contract tests, auth/permission tests, frontend interaction tests, build/typecheck/lint, smoke/e2e tests for critical flows.
- High-risk surfaces: auth, billing, privacy, public API compatibility, schema migrations, production data, external integrations, deployment/publishing.
- Common slice patterns: domain model characterization, auth boundary, core workflow test, API integration, UI state implementation, observability, e2e smoke, deployment readiness.

### CLI/library

- Discriminating questions: public API, compatibility shims, command surface, JSON output contracts, package targets, supported runtimes, documentation generation, migration policy.
- Likely design needs: docs hierarchy, examples, terminal output clarity, error-message style.
- Validation baseline: unit tests, integration tests, command snapshot/JSON tests, typecheck, lint, docs check, compatibility tests.
- High-risk surfaces: public API changes, command output shape, schema/storage layout, packaging, deprecation/removal.
- Common slice patterns: characterization tests, API shim alignment, command behavior, docs generation, capability manifest, release notes.

### Mobile/desktop app

- Discriminating questions: target platforms, native/web stack, device capabilities, offline behavior, notifications, permissions, app-store or notarization path, accessibility, telemetry.
- Likely design needs: platform conventions, navigation, responsive/adaptive layout, touch/keyboard input, native component patterns, iconography, motion.
- Validation baseline: build/test for target platform, UI smoke, accessibility checks, simulator/device validation, packaging/signing checks when applicable.
- High-risk surfaces: device permissions, app-store publishing, credentials, user data, push notifications, platform signing.
- Common slice patterns: platform scaffold, navigation shell, key flow, persistence/offline, permissions, packaging, simulator/device QA.

### Data/automation workflow

- Discriminating questions: data sources, schemas, schedules, transformations, idempotency, retry behavior, observability, error handling, storage, manual review points.
- Likely design needs: operator dashboard, logs/reports, status tables, alert presentation, artifact readability.
- Validation baseline: fixture-based data tests, schema validation, dry-run, idempotency checks, error-path tests, output artifact validation.
- High-risk surfaces: destructive data writes, credentials, production data, personal data, external automation side effects, irreversible actions.
- Common slice patterns: source connector, parser/schema, transform, dry-run report, scheduler, alerting, recovery path.

### Clinical/regulated workspace

- Discriminating questions: regulated data type, privacy boundary, local-only requirements, operation context, audit logging, deidentification, retention, access control, synthetic test strategy.
- Likely design needs: conservative workflow UI, clear status labels, audit evidence tables, accessible review surfaces, no real PHI in examples or screenshots.
- Validation baseline: policy tests, synthetic-only fixture review, audit/event checks, local-only checks, export/deidentification tests, docs check.
- High-risk surfaces: PHI/private data, credentials, cloud storage, telemetry, static export, production data, legal/compliance labels.
- Common slice patterns: policy gate, synthetic fixture, audit trail, local-only enforcement, export guard, compliance docs.

### Infrastructure/devops tooling

- Discriminating questions: target environment, provisioning tool, secrets model, deployment topology, rollback, observability, access boundaries, blast radius, cost constraints.
- Likely design needs: runbooks, topology diagrams, status dashboards, alert/incident views.
- Validation baseline: plan/dry-run, config validation, tests for generators, least-privilege checks, smoke checks, rollback verification where safe.
- High-risk surfaces: production infrastructure, credentials/secrets, DNS, destructive operations, cost, public exposure, account-level permissions.
- Common slice patterns: inventory, plan validation, secret handling, environment scaffold, deployment smoke, monitoring, rollback runbook.

## Project-Local Overrides

Project-local overrides may:

- Add a new archetype.
- Narrow a default archetype for the project.
- Add project-specific discriminating questions.
- Change validation baseline inside higher-priority repo/user/safety constraints.
- Add or remove design pathway prompts for the project.

Overrides must be recorded in project-local memory or project overlay artifacts. They do not mutate the built-in plugin registry.
