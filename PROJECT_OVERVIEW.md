# JARVIS Project Overview

## 1. Vision

JARVIS is a persistent personal AI system designed to operate across the user's devices and environment with a high degree of usefulness while remaining explicitly governed by identity, permissions, privacy rules, and recoverable system state.

The goal is broader than a voice assistant or chatbot. JARVIS is intended to combine:

- conversational interaction;
- persistent memory;
- multi-user identity and permissions;
- perception of authorized environmental inputs;
- a world model that represents what JARVIS currently believes and how certain it is;
- task planning and execution;
- proactive/initiative-driven assistance;
- notifications and escalation;
- extensible capabilities;
- local-first processing;
- optional cloud AI;
- auditable, bounded autonomy.

## 2. Confirmed v0.1 Direction

| Area | Decision | Status |
|---|---|---|
| Core hosting | Self-hosted JARVIS core | **CONFIRMED** |
| AI providers | Optional cloud AI may augment the core | **CONFIRMED** |
| Autonomy | Permission-governed autonomy | **CONFIRMED** |
| Data handling | Local-first processing/storage with explicit cloud exceptions | **CONFIRMED** |
| Deployment | Develop locally first, then scale to homelab | **CONFIRMED** |
| Users | Multi-user system | **CONFIRMED** |
| Guests | Guests can make permitted requests with reduced permissions | **CONFIRMED** |
| Permission ownership | Owner can grant/revoke user capabilities | **CONFIRMED** |
| Authorization | Authorization independent of the reasoning/model layer | **REQUIRED** |
| Model architecture | Models should be replaceable | **REQUIRED** |
| Tasks | Long-running work should be recoverable | **REQUIRED** |
| Memory | Scoped, provenance-aware memory | **REQUIRED** |
| World state | Evidence/inference uncertainty must be represented | **REQUIRED** |
| Failure handling | Failures must be observable | **REQUIRED** |

## 3. Intended User Experience

JARVIS is intended to feel continuously available and context-aware rather than requiring every action to begin with a fresh prompt.

Established interaction goals include:

- accept direct requests from the owner and other authorized users;
- adapt behavior to the current user and relationship/permission scope;
- monitor authorized inputs such as cameras and potentially EDITH-style wearable glasses;
- continuously scan authorized context for useful work or events;
- proactively initiate assistance where policy allows;
- use different notification channels based on urgency;
- place a phone call for urgent events and use text for non-urgent events, subject to capability availability and permission;
- remember information across sessions without collapsing all information into one unscoped conversation transcript.

These are product goals. Exact implementations, hardware, and integrations remain open unless specifically marked confirmed elsewhere.

## 4. Multi-User Model

JARVIS must support multiple principals.

Examples discussed:
- **Owner** — highest administrative authority.
- **Trusted people** — may receive broader capabilities configured by the owner.
- **Guests** — may interact but have fewer permissions.

The system must not assume that because a person can speak to JARVIS, that person can access the owner's private context or privileged capabilities.

Relationship information may help JARVIS communicate more naturally, but relationship inference must not override authorization.

## 5. Social and Person Context

The project discussed a future ability for JARVIS to build context about people it meets so it can understand social dynamics and behave more naturally.

Examples discussed:
- creating a baseline/person record for a newly encountered person;
- learning from observed interactions;
- modeling relationships between people;
- optionally using public internet information to enrich a person's profile.

**Status:** mixed.

- Multi-user identities and permission scopes are **CONFIRMED**.
- Social adaptation and relationship-aware behavior are part of the established design direction.
- Automatic public-internet research on people is **PROPOSED**, not a confirmed default.
- Consent, provenance, uncertainty, privacy isolation, revocation, and sensitive-profile limitations must be preserved.
- The final rules for automatic profile creation/research remain **OPEN**.

## 6. Environmental Awareness and Initiative

JARVIS is intended to be able to consume authorized observations and maintain a changing representation of the environment.

Examples discussed:
- authorized cameras;
- EDITH-style wearable glasses;
- persistent awareness;
- proactive detection of noteworthy events;
- proactive suggestions or tasks.

This must be implemented as bounded initiative, not unrestricted autonomous action.

A perception or model inference is not automatically a fact. JARVIS should track:
- source;
- observation time;
- confidence/uncertainty;
- staleness;
- inferred vs directly observed state.

## 7. Historical JARVIS Context

Earlier JARVIS experiments included:
- Porcupine/Picovoice wake-word detection for "Jarvis";
- speech input/output;
- a GUI concept with animated speaking state;
- simple text-file conversation history;
- later interest in Ollama/local models;
- extensible functions/capabilities;
- persistent storage;
- remote access;
- a Spotify-style capability invocation experiment.

These are **LEGACY** implementation experiments and inspirations. They are not automatically binding for the new architecture.

In particular, v0.1 should not reproduce the old design where an ever-growing raw conversation transcript is simply injected into the model at startup. That approach was already found to create latency/token problems.

## 8. Non-Goals for the First Implementation Phase

The initial implementation should not attempt to finish every envisioned JARVIS feature at once.

The first target is a trustworthy kernel that can:
- represent principals and sessions;
- authorize actions;
- register and execute capabilities;
- persist events/tasks/memory;
- recover task state;
- distinguish evidence from inference;
- expose audit information;
- run with local components;
- permit later model, sensor, and integration expansion.

## 9. Guiding Principle

JARVIS should become more capable by adding adapters, capabilities, models, sensors, and policies around a stable permission-aware kernel — not by giving an increasingly powerful model unrestricted access to the host system.
