# Blocking Policy

## Blocking Conditions

A task is blocked when continuing would require one of:

- A user decision the spec leaves open.
- Real PHI, private data, production credentials, unclear credential authorization, or an external system not already authorized.
- Computer-use, GUI-driving, or human-desktop-control work without user supervision.
- Destructive filesystem or git history actions that would discard user-authored work.
- Removing or narrowing a public compatibility surface without approval.
- Changing clinical local-only boundaries without a validated policy decision.
- Network, package, or API information that is likely stale and cannot be verified in the current context.
- An expected skill, plugin, browser capability, tool, credential source, route, or external resource should be available for the current slice but is not.
- The planned slice overlaps a dirty worktree path whose ownership is unclear or classified as user/unowned work.

## Mandatory Problem-Solving Escalation Ladder

Do not classify ordinary implementation difficulty as blocked until this escalation ladder has been followed in order:

1. Inspect local repo source, tests, logs, generated docs, BMAD artifacts, and Agentic Wiki memory.
2. Inspect package/tool output and official project documentation.
3. Use web search or current official ecosystem docs for non-sensitive technical questions.
4. Use installed skills/plugins and scoped browser or Chrome-control verification for development surfaces.
5. Use parallel worker review or adversarial review.
6. Split the task into smaller slices, continue a safe independent slice, or add the item to the supervision queue.

The order is mandatory unless a step is prevented by a time, budget, safety, permission, privacy, or task-applicability boundary. Record every attempted step, skipped step, and skip reason in the blocker note or run-state evidence when a task still cannot proceed.

## Resource Gap Handling

Classify missing resources before blocking:

- Expected resource unavailable: the current slice requires a resource that should already be available. Stop that slice, notify the user in chat, record the blocker, and move to an independent slice when one exists.
- Future resource opportunity: a useful resource does not exist yet. Do not block solely for that absence; use available substitutes, record it in `_bmad-output/implementation-artifacts/future-resource-queue.html`, and note to the user that creating the resource later would help.

If the future resource would improve the reusable plugin, record it in `_bmad-output/implementation-artifacts/plugin-improvement-queue.html` as a candidate when enough evidence exists.

Maintain the living future-resource queue at:

```text
_bmad-output/implementation-artifacts/future-resource-queue.html
```

Use it for resources that would help future work but do not exist yet. Do not use it for resources that should already exist but are unavailable; those are blockers for the affected slice.

Future-resource entries remain advisory until explicitly promoted by the user or an explicit plugin-development workflow. Keep the queue chronological and do not triage entries with priority or status fields.

## Not Blocked

A task is not blocked only because:

- Full validation is slow; run focused validation first, then full validation before closeout.
- The primary implementation path is unclear; write characterization tests or inspect the public API surface.
- Docs drift exists; identify exact generated docs and the command that should refresh them.
- A parallel worker is waiting; move to an independent queue item.
- Branching, committing, pushing, opening a PR, or continuing fallback work is needed and no active user instruction narrows that autonomy.
- Another slice has a blocker or failed validation and the current slice is independent of that failure.
- A validation failure still has one or two authorized self-repair attempts remaining inside the affected slice.
- The user has returned while an active safe task is in progress; finish the current task or stop at a reasonable point, then prioritize the supervision queue.
- A useful resource does not exist yet; continue with available resources and record the future-resource opportunity.
- A dirty worktree exists but all paths are classified and the current slice does not overlap user/unowned or unclear paths.
- A reversible implementation-level decision is needed and the choice stays inside current requirements and safety boundaries; make the decision, record the rationale, and continue.
- A decision is already classified as `architecture` or `product` and settled by BMAD/spec/user artifacts; cite the authority source and continue.
- An unresolved `architecture` or `product` decision affects only one slice or a dependency chain; stop that slice and dependents, record the decision needed, and continue independent slices.
- The user voluntarily passed a credential in chat for development work; use it with best-effort non-persistence rather than blocking solely because it appeared in chat.
- The approved intent packet identifies a project-local env or JSON credential source and the slice can use that source without recording secret values, crossing production boundaries, or requiring a standardized plugin credential schema.

## Required Blocker Note

When blocked, record:

- Task ID and objective.
- Exact blocker.
- Evidence already gathered.
- Mandatory escalation ladder steps attempted and any skipped steps with reasons.
- Non-local source evidence used, including source URL or tool name, retrieval/check time, supported claim, and official/current-enough assessment.
- Paths touched.
- Paths intentionally not touched.
- Dirty-worktree classification for relevant paths, including owner or slice and preservation action.
- Safe fallback selected, or reason no fallback is safe.
- Independent slice selected next, or reason no independent slice exists.
- Dependent slices that must wait.
- Resource-gap classification: expected unavailable resource or future resource opportunity.
- Incident/exception log disposition: whether the blocker should append a metadata-only incident entry, and if PHI-aware, confirmation that no PHI or secret values are recorded.
- Reversible implementation-level decisions already made, with rationale.
- Validation repair attempts already made, including failing command, fix hypothesis, touched paths, validation rerun, and result.
- Slice-owned changes preserved or reverted, with reason.
- Unresolved architecture or product decisions that require higher authority.
- Credential-source or authorization issue, without recording any credential value.
- Question for the user, phrased as a decision.

## Fallback Selection

Choose fallback work in this order:

1. Continue the highest-priority independent parallel slice already identified.
2. Split the next broad work item into independent slices.
3. Tests that characterize current behavior.
4. Read-only repo analysis and file ownership mapping.
5. Docs or manifest drift inventory.
6. Review of current diff for contract violations.
7. Story decomposition for future implementation.

## Escalation

Ask the user before crossing any stop condition in `task-graph.md`. Do not use fallback work to hide a decision that changes product scope, PHI boundary, public API compatibility, or git publishing behavior.

Pause the whole run for an unresolved architecture or product decision only when it affects the run boundary, shared architecture, safety constraints, plugin-core mutation, PHI/privacy boundary, public API compatibility, or all remaining work. Otherwise, keep the unresolved decision scoped to the affected slice and continue independent work.

## Supervision Queue

Maintain the living HTML supervision queue at:

```text
_bmad-output/implementation-artifacts/supervision-queue.html
```

Use it for tasks that are valid work but require user supervision before execution. Examples include broad computer-use, human-desktop control, personal account login, external production changes, sensitive pages, purchases, publishing, messaging, or irreversible account actions.

When the user announces their return, append or update the supervision queue and present it at the next reasonable stopping point. User return does not automatically interrupt a safe active task, but once that task completes or pauses safely, supervision-required items become the next priority.

## Incident Log

Maintain the metadata-only incident/exception log at:

```text
_bmad-output/implementation-artifacts/incident-log/
```

Append an incident entry when a blocker involves an expected-but-unavailable resource, exhausted repair loop, stale worker lease, safety/privacy boundary, credential/secret exposure risk, suspicious behavior, abnormal coordinator event, whole-run stop, or PHI monitoring alert. Use evidence pointers and concise summaries only. Do not record PHI, credentials, tokens, passwords, raw secret values, hidden reasoning, or raw sensitive text.

If the project handles PHI, use the HIPAA-aware log variant and include the monitoring phase that applies. The log supports development best practices and user reminders for security scans and abnormal-behavior review; it is not a legal compliance attestation.
