# JARVIS — Codex Project Instructions

## Purpose

This repository implements **JARVIS**, a self-hosted personal AI system intended to behave as a persistent, permission-governed assistant rather than a single chat application.

This file is the primary instruction file for coding agents working in the repository.

## Source of Truth

Read these documents before making architectural changes:

1. `PROJECT_OVERVIEW.md`
2. `REQUIREMENTS.md`
3. `ARCHITECTURE.md`
4. `DECISIONS.md`
5. `MEMORY_SYSTEM.md`
6. `PERMISSIONS.md`
7. `ROADMAP.md`

When documents disagree, prefer:
1. Explicit **Confirmed** decisions in `DECISIONS.md`
2. **Required** requirements in `REQUIREMENTS.md`
3. Architectural constraints in `ARCHITECTURE.md`
4. Proposed items and roadmap sequencing

Do **not** silently convert proposals or open questions into architecture.

## Status Vocabulary

Documentation uses these states:

- **CONFIRMED** — explicitly accepted for JARVIS v0.1.
- **REQUIRED** — implementation behavior implied by confirmed decisions or explicitly established requirements.
- **PROPOSED** — discussed direction, not yet binding.
- **OPEN** — unresolved question requiring an owner decision.
- **LEGACY** — useful prior JARVIS implementation context, but not automatically part of v0.1.

Coding agents must preserve this distinction in code, comments, issues, migrations, and docs.

## Core v0.1 Constraints

The following are confirmed and must not be changed without an explicit architectural decision:

- The JARVIS core is **self-hosted**.
- Cloud AI is **optional**, not foundational.
- JARVIS uses **permission-governed autonomy**.
- Data processing and storage are **local-first**.
- Cloud processing requires an **explicitly allowed exception**.
- Development begins **locally**, then scales to the user's homelab.
- JARVIS is **multi-user**.
- The owner controls which users can perform which actions.
- Guests may interact with JARVIS but have fewer permissions than trusted users.
- Authorization must be independent from the language/reasoning model.
- Models must be replaceable.
- Long-running or multi-step work must be represented as recoverable tasks rather than hidden in one model call.
- Memory must be scoped and preserve provenance, consent/access information, and uncertainty where applicable.
- World-state representations must distinguish observations/evidence from inference and account for uncertainty/staleness.
- Privileged actions must be auditable.
- Failures must be observable rather than silently swallowed.

## Agent Operating Rules

### Before coding

For every non-trivial change:

1. Identify the requirement IDs affected.
2. Identify any ADR/decision that constrains the change.
3. Check whether the behavior is confirmed, proposed, or open.
4. Prefer the smallest implementation that satisfies confirmed requirements.
5. Avoid binding the system to a single model vendor, transport, database, or hardware platform unless a confirmed decision requires it.

### While coding

- Keep authorization outside model prompts.
- Treat model output as untrusted input to policy and execution layers.
- Do not let a model directly invoke privileged operating-system or external actions without validation and authorization.
- Use typed/versioned contracts for persistent or cross-component events.
- Make important state transitions explicit.
- Make retries idempotent where practical.
- Preserve enough metadata to explain why an action was proposed, authorized, executed, denied, or failed.
- Never hide privacy- or permission-relevant information only inside free-form text.
- Prefer interfaces that permit local implementations and optional cloud adapters.
- Default to local data paths.
- Do not send memory, camera data, audio, identity data, or other private context to a cloud model unless the policy layer explicitly permits that data class and destination.

### Tests

Add tests alongside each implemented requirement.

At minimum, important subsystems should include:
- unit tests for policy logic and schema validation;
- integration tests for task recovery and event durability;
- negative tests for permission denial and privilege escalation;
- tests for user isolation;
- tests that revoke permissions and verify subsequent privileged execution is denied;
- tests for memory scope/access filtering;
- tests for stale/uncertain world-state handling;
- tests for failure/audit visibility.

Where external devices or integrations are involved, provide a simulation or fake adapter when practical.

## Security and Privacy

JARVIS is intended to become highly privileged. Security is architectural, not optional.

Never:
- put authorization solely in an LLM system prompt;
- grant permissions because a model claims a user has them;
- infer permanent identity solely from one weak sensor observation;
- leak one user's private memory into another user's session;
- silently upload local/private data to third-party AI services;
- execute arbitrary generated shell commands as a privileged default;
- silently create sensitive personal profiles from public internet data.

## Implementation Style

Prefer a modular kernel with explicit interfaces for:
- identity/session context;
- policy/authorization;
- cognitive coordination;
- capability registry and execution;
- tasks;
- events;
- memory;
- observations/world state;
- initiative;
- communication/notification;
- audit/telemetry.

A monolithic "agent loop" may be used only as a temporary prototype behind interfaces that can be split later.

## Open Questions

Do not decide these implicitly in code:
- final schema definitions;
- exact authorization model and policy language;
- exact failure semantics;
- exact public APIs;
- database/storage technology;
- runtime scheduler/task engine;
- specific model stack;
- exact identity/authentication technologies;
- exact camera/EDITH hardware integration;
- cloud-provider choices;
- detailed public-profile research policy.

If implementation cannot proceed without one of these, create a clearly documented temporary abstraction or TODO linked to the open decision.

## Documentation Discipline

When a change settles an open architectural question:
1. add or update an ADR in `DECISIONS.md`;
2. update affected requirements;
3. update architecture diagrams/text;
4. record migration impact;
5. add acceptance tests.

Do not rewrite history: mark superseded decisions rather than deleting them.


## Development Workflow and Definition of Done

For each implementation task:

1. Read the applicable requirement IDs and architectural decisions.
2. Inspect the existing implementation before modifying files.
3. Identify the smallest complete, testable vertical slice.
4. Implement the change using existing contracts and abstractions.
5. Add or update tests covering normal behavior, failures, and relevant security boundaries.
6. Run the affected tests and report their actual results.
7. Update documentation when interfaces, assumptions, or behavior change.
8. Record unresolved architectural decisions rather than silently resolving them.

A task is complete only when:

- Its claimed functionality is actually implemented.
- Relevant tests pass, or failures are explicitly reported.
- No authorization or data-isolation boundaries are bypassed.
- Architectural changes are documented.
- No secrets or private user data are introduced into the repository.
- Remaining limitations are identified.

Avoid creating empty abstractions solely to make the architecture appear complete. Prefer a smaller number of working components with verified contracts.

Preserve long-term extensibility, but do not introduce complexity without a demonstrated requirement.

For every substantial implementation, report the requirement IDs addressed, files changed, tests run, decisions made, and recommended next step.
