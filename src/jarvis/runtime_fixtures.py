"""Explicit opt-in development fixtures, never automatically activated by serve."""

from uuid import uuid4

from jarvis.core.contracts import AccessScope
from jarvis.core.initiative_records import InitiativePolicy
from jarvis.core.notification_records import NotificationPolicy, NotificationRecipient
from jarvis.core.world_records import SourceRegistration


def configure_demo_fixtures(app, token):
    owner = app.identities.owner_id
    app.permissions.grant(token, owner, "world.ingest", correlation_id=uuid4())
    app.world.register_source(
        SourceRegistration(
            source_id="runtime.garage.sensor",
            principal_id=owner,
            access_scope=AccessScope(owner_id=owner),
            observation_types=("motion",),
            entities=("garage",),
            location="garage",
            ttl_seconds=3600,
        ),
        session_token=token,
    )
    app.initiatives.configure(
        InitiativePolicy(
            policy_id="runtime.garage",
            principal_id=owner,
            target_id=owner,
            source_id="runtime.garage.sensor",
            entity_id="garage",
            location="garage",
            attribute="motion",
            expected_value=True,
            mode="APPROVAL_REQUIRED",
            capability_id="development.simulated_notice",
            arguments={"text": "Garage motion observed"},
            max_age_seconds=3600,
            expires_seconds=3600,
            cooldown_seconds=1,
        ),
        session_token=token,
    )
    app.notifications.configure(
        NotificationPolicy(
            policy_id="runtime.initiatives",
            origin_id=owner,
            recipient=NotificationRecipient(principal_id=owner),
            source_kind="initiative",
            channels=("simulated-push",),
            cooldown_seconds=0,
        ),
        session_token=token,
    )
