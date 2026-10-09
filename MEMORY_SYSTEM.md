# JARVIS Memory System

## 1. Purpose

JARVIS memory must provide useful continuity without turning all historical information into an unscoped global transcript.

The memory system is both a usability feature and a security boundary.

## 2. Confirmed Principles

- Memory persists across sessions.
- Memory is local-first.
- Memory is scoped.
- Memory preserves provenance.
- Consent/policy information must be represented where relevant.
- Memory use is permission-aware.
- Derived/inferred information must be distinguishable from direct evidence.
- Temporal validity matters.
- Retention/deletion behavior must be representable.
- A user's private memory must not automatically be available to another user.
- Memory should be retrieved selectively rather than wholesale-injected at startup.

## 3. Historical Lesson

An early JARVIS prototype appended timestamped User/JARVIS conversation pairs to a text file and considered loading a large recent history into every model session.

That approach was found to create startup latency and token-cost problems.

The v0.1 architecture should therefore treat raw conversation logs as source records, not as the primary memory retrieval strategy.

## 4. Logical Memory Types

The following are useful logical categories. They are a design model, not a mandated database schema.

### Interaction memory
Past conversations, requests, responses, and outcomes.

### User/person memory
Facts, preferences, relationship context, communication style, and other allowed person-specific information.

### Task memory
What work was requested, completed, failed, deferred, or is still in progress.

### Episodic/event memory
Noteworthy events with time, participants, sources, and outcomes.

### Semantic memory
Consolidated facts or summaries derived from one or more sources.

### World-state evidence
Observations that may contribute to the current world model.

## 5. Memory Record Contract

A memory record should be able to represent:

```yaml
memory_id: stable identifier
kind: interaction | person | task | episode | semantic | observation-derived | ...
subject_refs: [principal/entity ids]
content: structured payload or reference
created_at: record creation time
valid_from: optional effective time
valid_to: optional end of validity
source:
  type: user_statement | observation | capability_result | derived | import | ...
  ref: source identifier
derived_from: [source ids]
confidence: optional uncertainty/confidence
access_scope: policy/scope reference
consent_basis: optional consent/policy reference
retention_policy: optional
deletion_state: active | tombstoned | deleted-reference | ...
model_provenance:
  provider/model/version: optional
tags: [...]
schema_version: ...
```

**Exact field names and storage technology are OPEN.**

## 6. Retrieval Rules

Memory retrieval should consider at least:

1. authenticated principal/session;
2. access scope/policy;
3. task/conversation context;
4. relevance;
5. temporal validity;
6. staleness;
7. confidence;
8. data classification;
9. cloud-egress rules if retrieved memory would be sent to cloud AI.

Authorization filtering must happen before protected memory is returned to a model or interface.

## 7. Memory Write Pipeline

Recommended logical flow:

```text
source event / conversation / observation
        ↓
candidate extraction
        ↓
classification + source/provenance
        ↓
policy / consent check
        ↓
dedupe / conflict handling
        ↓
memory write
        ↓
audit event if policy-sensitive
```

This flow is consistent with established requirements, but the exact extraction technology is open.

## 8. Conflicts and Uncertainty

JARVIS must not overwrite uncertain or contradictory information as if it were unquestionably true.

A future implementation should support:
- multiple claims about the same fact;
- source comparison;
- confidence;
- supersession;
- effective time;
- explicit "unknown".

Exact conflict-resolution logic is **OPEN**.

## 9. Person and Relationship Memory

JARVIS is intended to become socially aware.

Potential person records may include:
- identity references;
- names/aliases;
- owner-defined relationship;
- communication preferences;
- allowed shared context;
- observed interaction patterns;
- relationship edges between people.

### Critical boundary

Relationship knowledge does **not** equal authorization.

For example, "best friend" may be useful context, but the permissions for that principal must still come from the policy system.

## 10. New-Person Baselines

The user proposed that JARVIS create a baseline when meeting a new person and learn from interactions.

**Status: PROPOSED.**

The system must not silently treat this as permission for unrestricted profiling.

Open decisions include:
- minimum record created automatically;
- identity confidence threshold;
- consent requirements;
- data classes allowed;
- visibility/deletion rights;
- retention.

## 11. Public Internet Research on People

The user proposed that JARVIS might enrich a person profile using internet searches.

**Status: PROPOSED / OPEN.**

Any future implementation must:
- store sources/provenance;
- preserve uncertainty;
- distinguish public-source claims from direct observations;
- enforce sensitive-data policy;
- respect configured consent/permission;
- allow access revocation;
- avoid exposing one person's profile to unauthorized principals.

Automatic research is not currently a confirmed default.

## 12. Memory and Cloud AI

Before protected memory is included in a cloud model request, the system must evaluate:
- whether cloud use is allowed;
- which provider is allowed;
- which data classes may leave the local system;
- whether redaction/minimization is required;
- which principal/task authorized the use.

Cloud egress should be auditable.

## 13. Deletion and Revocation

The detailed deletion model is still open, but the architecture must support:
- access revocation;
- retention rules;
- logical deletion/tombstones if needed for audit integrity;
- removal from normal retrieval;
- provenance updates when source data is removed or invalidated.

## 14. Implementation Priority

For the first kernel:
1. persistent memory interface;
2. scoped retrieval;
3. provenance;
4. principal isolation;
5. temporal fields;
6. consent/policy reference;
7. tests for cross-user leakage;
8. fake/in-memory backend for tests;
9. production local backend later.

Do not start by building automatic social profiling or internet enrichment.
