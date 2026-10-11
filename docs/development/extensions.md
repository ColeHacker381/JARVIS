# Extending the kernel

These are trusted host SDK examples using an existing `Application` named `app`
and a current owner credential named `token`. Registration is composition-time,
never model-generated. Test changes with the existing fake adapters first.

## 1. Capability adapter

Register metadata and a callable together, with an exact permission and bounded
JSON Schema input/output. Submit through `TaskManager`, never call the provider
from HTTP or cognition. Claim idempotency only if the adapter implements it.

```python
from jarvis.core.contracts import Capability
from jarvis.core.task_records import TaskStep


def uppercase(arguments, *, idempotency_key=None):
    return {"text": arguments["text"].upper()}


schema = {
    "type": "object",
    "properties": {"text": {"type": "string", "maxLength": 128}},
    "required": ["text"],
    "additionalProperties": False,
}
app.registry.register(
    Capability(
        capability_id="example.uppercase",
        adapter_ref="example.pure",
        required_permissions=("example.uppercase",),
        input_schema=schema,
        output_schema=schema,
    ),
    uppercase,
    supports_idempotency=True,
)
task = app.tasks.submit(
    (
        TaskStep(
            step_id="uppercase", capability_id="example.uppercase", arguments={"text": "hello"}
        ),
    ),
    session_token=token,
)
result = app.tasks.run(task.task_id, session_token=token)
```

Without an explicit current grant this is denied and `uppercase` is never called.
Add private scope where needed. Internal transport capabilities also require a
guard family; do not expose their IDs through conversational discovery.

### Permission administration

Use the existing owner-only PermissionAdministration service, exact names and an
audit correlation ID. Permissions do not belong in prompts or capability output.

```python
from uuid import uuid4

owner = app.identities.owner_id
app.permissions.grant(token, owner, "example.uppercase", correlation_id=uuid4())
# Revocation must block the next handler invocation, including recovery.
app.permissions.revoke(token, owner, "example.uppercase", correlation_id=uuid4())
```

New permission names need a requirement/security rationale, negative tests and
documentation. Owner role alone never satisfies a capability's permission.

## 2. Fake capability for tests

Use a small pure callable with an observed invocation list, the real registry,
PermissionAdministration and TaskManager. This example uses the schema/imports
above; it executes trusted fixture setup, not a production privilege issuer.

```python
calls = []


def fake_echo(arguments, *, idempotency_key=None):
    calls.append(arguments["text"])
    return {"text": arguments["text"]}


app.registry.register(
    Capability(
        capability_id="example.fake_echo",
        adapter_ref="example.fake",
        required_permissions=("example.fake_echo",),
        input_schema=schema,
        output_schema=schema,
    ),
    fake_echo,
)
fake_step = TaskStep(step_id="fake", capability_id="example.fake_echo", arguments={"text": "hello"})
denied_task = app.tasks.submit((fake_step,), session_token=token)
denied_result = app.tasks.run(denied_task.task_id, session_token=token)
assert denied_result.task.state == "FAILED" and calls == []
app.permissions.grant(token, owner, "example.fake_echo", correlation_id=uuid4())
allowed_task = app.tasks.submit((fake_step,), session_token=token)
allowed_result = app.tasks.run(allowed_task.task_id, session_token=token)
assert allowed_result.task.state == "COMPLETED" and calls == ["hello"]
app.permissions.revoke(token, owner, "example.fake_echo", correlation_id=uuid4())
```

Also test malformed input/output schemas, current revoked credentials, scope,
timeout/error normalization and zero handler invocations for every denied request.
Fake effects are synthetic; they cannot prove a real hardware/transport outcome.

## 3. Sensor source

Enroll through `WorldModel.register_source` with current `world.sources.manage`;
ingest with the source's current authenticated principal and `world.ingest`.
There is no anonymous sensor enrollment route. Device fields cannot choose ACLs.

```python
from jarvis.core.contracts import AccessScope
from jarvis.core.world_records import SourceRegistration, ObservationInput, SensorPayload
from jarvis.core.time import utc_now

app.world.register_source(
    SourceRegistration(
        source_id="example.sensor",
        principal_id=owner,
        access_scope=AccessScope(owner_id=owner),
        observation_types=("motion",),
        entities=("study",),
        location="study",
        ttl_seconds=60,
    ),
    session_token=token,
)
record, replayed = app.world.ingest(
    ObservationInput(
        source_id="example.sensor",
        entity_id="study",
        location="study",
        observation_type="motion",
        observed_at=utc_now(),
        confidence=0.8,
        payload=SensorPayload(value=True),
    ),
    session_token=token,
)
```

Test duplicate IDs, changed payloads, expired evidence, source revocation and
cross-user reads. Keep observation evidence distinct from inferred occupancy;
synthetic confidence is not a calibrated probability. Real sensors are deferred.

## 4. Model provider

Implement `ModelProvider.identity` and async `complete(ModelRequest) -> str`.
Return untrusted CognitiveDecision JSON; use ModelError for normalized errors,
respect timeout/cancellation, and never receive bearer tokens in model requests.

```python
from jarvis.core.cognitive_records import CognitiveDecision, ModelIdentity
from jarvis.models import ModelRouter


class ExampleFakeModel:
    identity = ModelIdentity(
        provider_id="example.fake",
        model_id="constant-v1",
        location="fake",
        destination="in-process",
    )

    async def complete(self, request):
        return CognitiveDecision(kind="direct", text="Explicit example fake.").model_dump_json()


router = ModelRouter({"example": ExampleFakeModel()}, selected="example", timeout=5)
```

Bind the router in the shared composition root rather than replacing coordinator
authorization. Cloud adapters additionally require trusted EgressPolicy; no
fallback is implied. Test invalid JSON, forged claims, timeout and zero denied
adapter calls. Local HTTP protocol fixtures do not establish genuine inference.

## 5. Notification channel

Implement `DeliveryChannel.metadata` and `send(NotificationDeliveryRequest)` with
a typed `NotificationDeliveryResult`. Supply channels to `build_application` at
construction so NotificationService installs guarded internal capabilities once.

```python
from jarvis.notification_channels import SimulatedPushChannel

# database is the same SQLiteDatabase used by the shared composition root.
channel = SimulatedPushChannel(database, channel_id="example-simulated")
# build_application(path, notification_channels=(channel,), ...)
```

Metadata pins destination, provider and replay semantics. The current service
admits only local/simulated destinations. Add the channel to explicit owner
policy AND recipient preferences; registration alone authorizes nothing. Use
actual attempt/effect crash tests, quiet hours and revoked-source checks. A real
external channel requires a future authorized milestone and egress/address rules.

## 6. Initiative rule/evaluator

Phase 1 `InitiativeEvaluator.suggest(session, observations)` is a legacy proposal
protocol. Current `InitiativeEngine` uses `ModelInitiativeGenerator.suggest(policy,
state)` behind trusted deterministic policy checks; it returns InitiativeCandidate,
never approval. Extend that replaceable generator at composition time if needed.
For most rules, configure the existing engine instead of introducing a new loop:

```python
from jarvis.core.initiative_records import InitiativePolicy

app.initiatives.configure(
    InitiativePolicy(
        policy_id="example.study",
        principal_id=owner,
        target_id=owner,
        source_id="example.sensor",
        entity_id="study",
        location="study",
        attribute="motion",
        expected_value=True,
        mode="OBSERVE_ONLY",
        capability_id="development.simulated_notice",
        arguments={"text": "Study motion"},
    ),
    session_token=token,
)
```

The trusted catalog currently limits simulated actions/risks. New arbitrary
capability IDs are not automatically eligible for autonomous execution. Test
observe-only/no handler, exact approval, stale evidence and revoked grants.

## 7. Persistent migration

Append a numbered migration to `storage.sqlite.MIGRATIONS` and increment
SCHEMA_VERSION. Never edit a migration already shipped. Initialization executes
missing versions atomically and rejects future versions. Preserve ownership,
grant/session revocations, task guard references and historical journal records.

Use a small old-schema database fixture, apply `SQLiteDatabase.initialize()` twice,
verify current version/data/security, then exercise old records through domain
services. Also test rollback on a failing statement and future-version rejection.
Do not create grants or re-bootstrap authority in a migration. Stop writers,
take an online backup, and use `jarvis db-init --database <existing-path>` explicitly.
`serve` never migrates automatically.

The following runnable host example verifies applying all currently shipped
migrations is repeatable. Use an isolated store/backup; it does not define a new
version or authorize modifications to an active runtime.

```python
from jarvis.storage.sqlite import SCHEMA_VERSION

database.initialize()
database.initialize()
with database.transaction() as connection:
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
database.verify()
```

## 8. Versioned contract

Extend an existing domain contract where semantics fit. New contracts inherit
`Contract`, use explicit version validation and a registered domain decoder.
For cross-process input, require the serialized integer version before validation;
constructor defaults alone do not validate a missing wire version.

```python
from typing import Literal
from jarvis.core.contracts import Contract


class ExampleReading(Contract):
    contract_type: Literal["example_reading"] = "example_reading"
    value: int


wire = ExampleReading(value=3).model_dump_json()
reading = ExampleReading.model_validate_json(wire)
```

This example is not registered for generic decode or persistence. An actual
extension must add its discriminator to the appropriate decoder, check explicit
versions at entry, document compatibility/migration, and test missing/bool/string/
unsupported versions plus extra fields. IDs/records are data, never authority.
