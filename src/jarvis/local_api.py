"""Authenticated loopback transport. All private operations delegate to core services."""

import asyncio
import ipaddress
import json
import time
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from jarvis.api_contracts import (
    ApiFailure,
    ApiResponse,
    Health,
    HistoryEntry,
    MemorySearchRequest,
    MessageRequest,
    NotificationPage,
    ObservationAcceptance,
    ObservationRequest,
    OperationalStatus,
    RequestV1,
    RevisionRequest,
    TaskSummary,
)
from jarvis.conversations import ConversationError
from jarvis.core.cognitive_records import CognitiveResponse, Conversation
from jarvis.core.contracts import ErrorDetail
from jarvis.core.initiative_records import InitiativeRecord
from jarvis.core.memory_records import MemoryQuery, MemoryRecord, MemorySearchResult
from jarvis.core.notification_records import Notification, NotificationDelivery
from jarvis.core.world_records import WorldState
from jarvis.identity import IdentityError
from jarvis.initiative import InitiativeError
from jarvis.memory import MemoryError
from jarvis.notifications import NotificationError
from jarvis.observability import AuditWriteError
from jarvis.runtime_service import authoritative_failure
from jarvis.storage.errors import StorageError
from jarvis.world_state import WorldError


def failure(request_id, code, status):
    return JSONResponse(
        ApiFailure(request_id=request_id, error=ErrorDetail(code=code, message=code)).model_dump(
            mode="json"
        ),
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Request-ID": str(request_id)},
    )


class LocalBoundary:
    """Host/origin/body/admission bounds before credentials or domain invocation."""

    def __init__(self, app, runtime):
        self.app, self.runtime = app, runtime
        self.active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4()
        scope.setdefault("state", {})["request_id"] = request_id
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        host = headers.get("host", "").lower()
        address = host.split("]", 1)[0] + "]" if host.startswith("[") else host.split(":", 1)[0]
        valid_host = address in {"127.0.0.1", "[::1]", "localhost"}
        if not valid_host:
            return await failure(request_id, "host_denied", 403)(scope, receive, send)
        if "origin" in headers or headers.get("sec-fetch-site", "none") != "none":
            return await failure(request_id, "browser_origin_denied", 403)(scope, receive, send)
        if any(k in headers for k in ("x-identity", "x-principal-id", "x-session-id")):
            return await failure(request_id, "identity_claim_forbidden", 403)(scope, receive, send)
        # Uvicorn does not trust forwarded headers; actual socket clients must be local.
        client = scope.get("client")
        if client is not None:
            try:
                local = ipaddress.ip_address(client[0]).is_loopback
            except ValueError:
                local = client[0] == "testclient"  # ASGI in-process tests, no network listener.
            if not local:
                return await failure(request_id, "local_client_required", 403)(scope, receive, send)
        if self.active >= self.runtime.settings.max_requests:
            return await failure(request_id, "runtime_busy", 503)(scope, receive, send)
        self.active += 1
        started = time.monotonic()
        try:
            body = bytearray()
            deadline = time.monotonic() + self.runtime.settings.request_body_timeout_seconds
            while True:
                try:
                    message = await asyncio.wait_for(
                        receive(), timeout=max(0, deadline - time.monotonic())
                    )
                except TimeoutError:
                    return await failure(request_id, "request_body_timeout", 408)(
                        scope, receive, send
                    )
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > self.runtime.settings.request_body_limit_bytes:
                    return await failure(request_id, "request_too_large", 413)(scope, receive, send)
                if not message.get("more_body"):
                    break
            delivered = False
            response_started = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            async def safe_send(message):
                nonlocal response_started
                if message["type"] == "http.response.start":
                    response_started = True
                    message["headers"] = [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower()
                        not in {b"cache-control", b"x-request-id", b"x-content-type-options"}
                    ]
                    message["headers"].extend(
                        [
                            (b"cache-control", b"no-store"),
                            (b"x-request-id", str(request_id).encode()),
                            (b"x-content-type-options", b"nosniff"),
                        ]
                    )
                await send(message)

            try:
                await self.app(scope, bounded_receive, safe_send)
            except Exception:
                # Framework fallback may rethrow after the sanitized response.
                # Raw exception messages must never reach server logs.
                self.runtime.log.error(
                    "runtime.request_failed", extra={"reason_code": "operation_failed"}
                )
                if not response_started:
                    await failure(request_id, "operation_failed", 500)(scope, receive, send)
        finally:
            self.active -= 1
            if self.runtime.application is not None:
                self.runtime.application.metrics.record(
                    "runtime.request_ms", (time.monotonic() - started) * 1000
                )


def create_api(runtime):
    @asynccontextmanager
    async def lifespan(app):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    api = FastAPI(
        title="JARVIS v0.9 development local API",
        version="0.9.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        responses={
            status: {"model": ApiFailure, "description": description}
            for status, description in {
                408: "Request body timeout",
                413: "Request body too large",
                500: "Sanitized operation failure",
            }.items()
        },
    )
    api.add_middleware(LocalBoundary, runtime=runtime)
    bearer = HTTPBearer(auto_error=False)
    bearer_dependency = Depends(bearer)

    async def credential(
        request: Request, value: HTTPAuthorizationCredentials | None = bearer_dependency
    ):
        if not runtime.public_health()["ready"]:
            raise HTTPException(503, "runtime_not_ready")
        if value is None or value.scheme.lower() != "bearer":
            raise HTTPException(401, "authentication_required")
        await runtime.call(runtime.application.identities.resolve_kind, value.credentials, "client")
        return value.credentials

    authentication = Depends(credential)

    def wrap(request, data):
        return ApiResponse(request_id=request.state.request_id, data=data)

    @api.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        code = {404: "route_unavailable", 405: "method_not_allowed"}.get(
            exc.status_code, str(exc.detail)
        )
        return failure(request.state.request_id, code, exc.status_code)

    @api.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return failure(request.state.request_id, "request_validation_failure", 422)

    @api.exception_handler(Exception)
    async def domain_error(request, exc):
        if authoritative_failure(exc):
            runtime.state, runtime.database_available = "FAILED", False
            runtime._stop.set()
            return failure(request.state.request_id, "authoritative_state_unavailable", 503)
        if isinstance(exc, RuntimeError) and exc.args in {
            ("runtime_busy",),
            ("runtime_not_ready",),
        }:
            return failure(request.state.request_id, exc.args[0], 503)
        if isinstance(exc, IdentityError):
            code = exc.reason_code
            return failure(
                request.state.request_id,
                code if code == "task_unavailable" else "authentication_invalid",
                404 if code == "task_unavailable" else 401,
            )
        if isinstance(
            exc, (ConversationError, MemoryError, WorldError, InitiativeError, NotificationError)
        ):
            code = exc.code
            status = (
                404
                if "unavailable" in code
                else 409
                if any(s in code for s in ("conflict", "replay", "transition", "busy"))
                else 403
            )
            return failure(request.state.request_id, code, status)
        return failure(request.state.request_id, "operation_failed", 500)

    for error_type in (
        IdentityError,
        ConversationError,
        MemoryError,
        WorldError,
        InitiativeError,
        NotificationError,
        StorageError,
        AuditWriteError,
    ):
        api.add_exception_handler(error_type, domain_error)

    health_responses = {
        403: {"model": ApiFailure, "description": "Local boundary denied"},
        503: {"model": Health | ApiFailure, "description": "Process/core unready or saturated"},
    }

    @api.get(
        "/health/live",
        response_model=Health,
        responses=health_responses,
        tags=["health"],
        description="Non-sensitive process liveness.",
    )
    async def live():
        health = runtime.public_health()
        return JSONResponse(health, status_code=200 if health["live"] else 503)

    @api.get(
        "/health/ready",
        response_model=Health,
        responses=health_responses,
        tags=["health"],
        description="Core readiness; optional model/worker degradation is explicit.",
    )
    @api.get("/health/status", response_model=Health, responses=health_responses, tags=["health"])
    async def ready():
        health = runtime.public_health()
        return JSONResponse(health, status_code=200 if health["ready"] else 503)

    responses = {
        status: {"model": ApiFailure, "description": description}
        for status, description in {
            401: "Current client credential required",
            403: "Current authorization denied",
            404: "Resource unavailable",
            409: "Revision, replay or transition conflict",
            422: "Request validation failed",
            503: "Core unavailable or admission saturated",
        }.items()
    }
    from fastapi import APIRouter

    routes = APIRouter(prefix="/api/v1", responses=responses)

    @routes.post("/conversations", response_model=ApiResponse[Conversation], tags=["conversations"])
    async def start(body: RequestV1, request: Request, token=authentication):
        return wrap(
            request,
            await runtime.call(runtime.application.conversations.start, session_token=token),
        )

    @routes.post(
        "/conversations/{conversation_id}/messages",
        response_model=ApiResponse[CognitiveResponse],
        tags=["conversations"],
        description=(
            "Persisted request_id is the owner/conversation-bound idempotency key. "
            "One model call, at most one validated action."
            " Optional Prefer: response-style=voice requests concise speech-oriented guidance; "
            "it grants no authority and never truncates verified execution results."
        ),
    )
    async def message(
        conversation_id: UUID, body: MessageRequest, request: Request, token=authentication
    ):
        result = await runtime.call(
            lambda: asyncio.run(
                runtime.application.coordinator.respond(
                    conversation_id,
                    body.text,
                    session_token=token,
                    request_id=body.request_id,
                    response_style=(
                        "voice"
                        if request.headers.get("prefer") == "response-style=voice"
                        else "text"
                    ),
                )
            )
        )
        if result.error and result.error.code in {"request_conflict", "conversation_unavailable"}:
            raise ConversationError(result.error.code)
        if result.error and authoritative_failure(result.error):
            raise StorageError(result.error.code)
        return wrap(request, result)

    @routes.get(
        "/conversations/{conversation_id}/history",
        response_model=ApiResponse[list[HistoryEntry]],
        tags=["conversations"],
    )
    async def history(
        conversation_id: UUID,
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        token=authentication,
    ):
        records = await runtime.call(
            runtime.application.coordinator.history,
            conversation_id,
            session_token=token,
            limit=limit,
        )
        return wrap(
            request,
            [
                {
                    "schema_version": 1,
                    "request_id": r.request_id,
                    "created_at": r.created_at,
                    "user_text": r.user_text,
                    "state": r.state,
                    "response": r.response,
                    "task_id": r.task_id,
                }
                for r in records
            ],
        )

    @routes.get("/tasks", response_model=ApiResponse[list[TaskSummary]], tags=["tasks"])
    async def tasks(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        after: UUID | None = None,
        token=authentication,
    ):
        records = await runtime.call(
            runtime.application.tasks.list, session_token=token, limit=limit, after=after
        )
        return wrap(request, [TaskSummary.from_task(t) for t in records])

    @routes.get("/tasks/{task_id}", response_model=ApiResponse[TaskSummary], tags=["tasks"])
    async def task(task_id: UUID, request: Request, token=authentication):
        result = await runtime.call(runtime.application.tasks.get, task_id, session_token=token)
        return wrap(request, TaskSummary.from_task(result))

    @routes.post("/memory/query", response_model=ApiResponse[MemorySearchResult], tags=["memory"])
    async def memories(body: MemorySearchRequest, request: Request, token=authentication):
        result = await runtime.call_timed(
            "memory.query_ms",
            runtime.application.memory.search,
            MemoryQuery(**body.model_dump()),
            session_token=token,
        )
        return wrap(request, result)

    @routes.get("/memory/{memory_id}", response_model=ApiResponse[MemoryRecord], tags=["memory"])
    async def memory(memory_id: UUID, request: Request, token=authentication):
        return wrap(
            request,
            await runtime.call_timed(
                "memory.query_ms", runtime.application.memory.get, memory_id, session_token=token
            ),
        )

    @routes.get("/world", response_model=ApiResponse[tuple[WorldState, ...]], tags=["world"])
    async def world(
        request: Request,
        entity: str | None = Query(None, max_length=96),
        location: str | None = Query(None, max_length=96),
        attr: str | None = Query(None, max_length=96),
        limit: int = Query(10, ge=1, le=20),
        token=authentication,
    ):
        return wrap(
            request,
            await runtime.call(
                runtime.application.world.query,
                session_token=token,
                entity=entity,
                location=location,
                attr=attr,
                limit=limit,
            ),
        )

    @routes.get("/world/{state_id}", response_model=ApiResponse[WorldState], tags=["world"])
    async def state(state_id: UUID, request: Request, token=authentication):
        return wrap(
            request,
            await runtime.call(runtime.application.world.get, state_id, session_token=token),
        )

    @routes.post(
        "/world/observations",
        response_model=ApiResponse[ObservationAcceptance],
        tags=["world"],
        description=(
            "Only pre-enrolled sources and current world.ingest grants; "
            "no enrollment or identity claims."
        ),
    )
    async def observe(body: ObservationRequest, request: Request, token=authentication):
        record, replayed = await runtime.call(
            runtime.application.world.ingest, body.observation, session_token=token
        )
        return wrap(request, ObservationAcceptance(record=record, replayed=replayed))

    @routes.get(
        "/initiatives",
        response_model=ApiResponse[tuple[InitiativeRecord, ...]],
        tags=["initiatives"],
    )
    async def initiatives(
        request: Request, limit: int = Query(20, ge=1, le=100), token=authentication
    ):
        return wrap(
            request,
            await runtime.call(
                runtime.application.initiatives.list, session_token=token, limit=limit
            ),
        )

    @routes.get(
        "/initiatives/{initiative_id}",
        response_model=ApiResponse[InitiativeRecord],
        tags=["initiatives"],
    )
    async def initiative(initiative_id: UUID, request: Request, token=authentication):
        return wrap(
            request,
            await runtime.call(
                runtime.application.initiatives.get, initiative_id, session_token=token
            ),
        )

    def initiative_mutation(initiative_id, body, token, deny):
        engine = runtime.application.initiatives
        if deny:
            return engine.deny(
                initiative_id, session_token=token, expected_revision=body.expected_revision
            )
        record = engine.approve(
            initiative_id, session_token=token, expected_revision=body.expected_revision
        )
        if record.state == "APPROVED":
            return engine.execute(initiative_id, session_token=token)
        return record

    @routes.post(
        "/initiatives/{initiative_id}/approve",
        response_model=ApiResponse[InitiativeRecord],
        tags=["initiatives"],
    )
    async def approve(
        initiative_id: UUID, body: RevisionRequest, request: Request, token=authentication
    ):
        return wrap(
            request, await runtime.call(initiative_mutation, initiative_id, body, token, False)
        )

    @routes.post(
        "/initiatives/{initiative_id}/deny",
        response_model=ApiResponse[InitiativeRecord],
        tags=["initiatives"],
    )
    async def deny(
        initiative_id: UUID, body: RevisionRequest, request: Request, token=authentication
    ):
        return wrap(
            request, await runtime.call(initiative_mutation, initiative_id, body, token, True)
        )

    @routes.get(
        "/notifications", response_model=ApiResponse[NotificationPage], tags=["notifications"]
    )
    async def notifications(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        after: int = Query(0, ge=0),
        text: str | None = Query(None, max_length=100),
        token=authentication,
    ):
        records, cursor = await runtime.call(
            runtime.application.notifications.list,
            session_token=token,
            limit=limit,
            after=after,
            text=text,
        )
        return wrap(request, NotificationPage(records=records, next_cursor=cursor))

    @routes.get(
        "/notifications/{notification_id}",
        response_model=ApiResponse[Notification],
        tags=["notifications"],
    )
    async def notification(notification_id: UUID, request: Request, token=authentication):
        return wrap(
            request,
            await runtime.call(
                runtime.application.notifications.get, notification_id, session_token=token
            ),
        )

    @routes.get(
        "/notifications/{notification_id}/deliveries",
        response_model=ApiResponse[tuple[NotificationDelivery, ...]],
        tags=["notifications"],
    )
    async def deliveries(notification_id: UUID, request: Request, token=authentication):
        return wrap(
            request,
            await runtime.call(
                runtime.application.notifications.deliveries, notification_id, session_token=token
            ),
        )

    # Register explicit operations; no arbitrary method dispatch supplied by a client.
    def interaction(operation):
        async def run(
            notification_id: UUID,
            body: RevisionRequest,
            request: Request,
            token=authentication,
        ):
            return wrap(
                request,
                await runtime.call(
                    runtime.application.notifications.interact,
                    notification_id,
                    operation,
                    expected_revision=body.expected_revision,
                    session_token=token,
                ),
            )

        return run

    for operation in ("read", "acknowledge", "dismiss"):
        routes.add_api_route(
            f"/notifications/{{notification_id}}/{operation}",
            interaction(operation),
            methods=["POST"],
            response_model=ApiResponse[Notification],
            tags=["notifications"],
            name="notification_" + operation,
            description=(
                "Revision checked inbox interaction. Acknowledgement never approves an initiative."
            ),
        )

    @routes.get("/status", response_model=ApiResponse[OperationalStatus], tags=["status"])
    async def status(request: Request, token=authentication):
        session = await runtime.call(runtime.application.identities.resolve_kind, token, "client")
        if session.principal_id != await runtime.call(
            lambda: runtime.application.identities.owner_id
        ):
            raise HTTPException(403, "operational_status_denied")
        summary = await runtime.call(runtime.operational_summary, token)
        # Internal telemetry retains None for unavailable provider fields. Status v1
        # exposes numeric samples only; missing timing is never fabricated as zero.
        summary["metrics"] = [sample for sample in summary["metrics"] if sample[1] is not None]
        return wrap(request, OperationalStatus.model_validate_json(json.dumps(summary)))

    @routes.get("/openapi.json", tags=["documentation"])
    async def schema(token=authentication):
        return get_openapi(title=api.title, version=api.version, routes=api.routes)

    api.include_router(routes)
    return api
