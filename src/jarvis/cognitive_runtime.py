"""Explicit development-only composition; never use this CLI authority for public clients."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from jarvis.authorization import DeterministicPolicy
from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.cognition import CognitiveCoordinator
from jarvis.context import ContextBuilder
from jarvis.conversations import ConversationService
from jarvis.core.contracts import Provenance
from jarvis.core.memory_records import MemoryDraft
from jarvis.development import (
    BRIGHTNESS_PERMISSION,
    ECHO_PERMISSION,
    register_development_capabilities,
)
from jarvis.memory import MemoryService
from jarvis.models import FakeModel, ModelRouter, OpenAILocalModel
from jarvis.storage.conversations import SQLiteConversationStore
from jarvis.storage.memory import SQLiteMemoryStore
from jarvis.storage.security import SQLiteDevelopmentIdentityService, SQLitePermissionStore
from jarvis.storage.sqlite import SQLiteAuditSink, SQLiteDatabase, SQLiteTaskStore
from jarvis.task_manager import TaskManager

FRIEND_ID = UUID("e3c4b46a-f7fb-43e6-a1be-000000000005")
GUEST_ID = UUID("e3c4b46a-f7fb-43e6-a1be-000000000006")


@dataclass
class DevelopmentCognitiveRuntime:
    database: SQLiteDatabase
    identities: SQLiteDevelopmentIdentityService
    permissions: SQLitePermissionStore
    memory: MemoryService
    conversations: ConversationService
    tasks: TaskManager
    coordinator: CognitiveCoordinator
    lighting: object
    audit: SQLiteAuditSink

    def credentials(self, name):
        principal_id = {"owner": self.identities.owner_id, "friend": FRIEND_ID, "guest": GUEST_ID}[
            name
        ]
        return self.identities.establish_session_for_testing(principal_id)


def development_runtime(
    path: Path,
    *,
    bootstrap=False,
    provider="fake",
    endpoint="http://127.0.0.1:11434/v1",
    model="qwen2.5:3b",
    timeout=60.0,
    fake=None,
    defer_actions=False,
) -> DevelopmentCognitiveRuntime:
    database = SQLiteDatabase(path)
    database.initialize()
    identities = SQLiteDevelopmentIdentityService(database)
    audit = SQLiteAuditSink(database)
    permissions = SQLitePermissionStore(identities, audit)
    memory = MemoryService(SQLiteMemoryStore(database, identities, audit), permissions)
    if bootstrap:
        owner = identities.bootstrap_owner_for_testing("Cognition development owner")
        identities.create_principal_for_testing(
            "Development trusted friend", principal_id=FRIEND_ID
        )
        identities.create_principal_for_testing(
            "Development guest", kind="guest", principal_id=GUEST_ID
        )
        credentials = identities.establish_session_for_testing(owner.principal_id)
        for principal_id in (owner.principal_id, FRIEND_ID, GUEST_ID):
            for permission in ("memory.read", ECHO_PERMISSION):
                permissions.grant(
                    credentials.token, principal_id, permission, correlation_id=uuid4()
                )
        for permission in ("memory.write", "memory.share", "memory.history", BRIGHTNESS_PERMISSION):
            permissions.grant(
                credentials.token, owner.principal_id, permission, correlation_id=uuid4()
            )
        permissions.grant(
            credentials.token, FRIEND_ID, BRIGHTNESS_PERMISSION, correlation_id=uuid4()
        )
        memory.create(
            MemoryDraft(
                kind="semantic",
                content={"text": "Slipstream uses microcontroller B"},
                project_ref="project:slipstream",
                source=Provenance(
                    category="user_statement", source_ref="development.cognition.fixture"
                ),
                consent_ref="consent:offline_fixture",
            ),
            session_token=credentials.token,
        )
        identities.close_session_for_testing(credentials.token)
    registry = InMemoryCapabilityRegistry()
    lighting = register_development_capabilities(registry)
    policy = DeterministicPolicy(identities, permissions, audit)
    from jarvis.execution import CapabilityExecutor

    executor = CapabilityExecutor(identities, registry, policy, audit)
    tasks = TaskManager(SQLiteTaskStore(database), identities, executor, audit)
    conversations = ConversationService(SQLiteConversationStore(database, identities, audit))
    context = ContextBuilder(
        identities=identities,
        memory=memory,
        conversations=conversations,
        registry=registry,
        policy=policy,
        tasks=tasks,
    )
    models = ModelRouter(
        {
            "fake": fake or FakeModel(),
            "local": OpenAILocalModel(endpoint=endpoint, model=model, timeout=timeout),
        },
        selected=provider,
        timeout=timeout,
    )
    coordinator = CognitiveCoordinator(
        conversations=conversations,
        context=context,
        models=models,
        tasks=tasks,
        audit=audit,
        defer_actions=defer_actions,
    )
    return DevelopmentCognitiveRuntime(
        database,
        identities,
        permissions,
        memory,
        conversations,
        tasks,
        coordinator,
        lighting,
        audit,
    )
