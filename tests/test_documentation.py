"""References and documented SDK/HTTP quickstart execute against actual interfaces."""

import asyncio
import re
from pathlib import Path
from uuid import uuid4

from jarvis.application import build_application
from jarvis.core.cognitive_records import ModelMessage, ModelRequest
from jarvis.core.task_records import TaskStep
from jarvis.documentation import check_documentation, openapi_reference
from jarvis.runtime_demo import run_quickstart

ROOT = Path(__file__).resolve().parents[1]


def test_internal_links_and_generated_configuration_cli_openapi_are_current():
    result = check_documentation(ROOT)
    assert result["generated_references"] == 3 and result["internal_links"] > 50
    content = (ROOT / "docs/reference/api-reference.md").read_text()
    documented = set(re.findall(r"\| (GET|POST) \| (/[\w/{}/.-]+) \|", content))
    actual = {
        (method.upper(), path)
        for path, operations in openapi_reference()["paths"].items()
        for method in operations
    }
    assert documented == actual


def test_extension_snippets_use_real_capability_sensor_model_and_contract_interfaces(tmp_path):
    app = build_application(tmp_path / "examples.sqlite3", bootstrap=True, provider="fake")
    token = app.credentials("owner").token
    app.permissions.grant(token, app.identities.owner_id, "world.ingest", correlation_id=uuid4())
    namespace = {"app": app, "token": token, "database": app.database}
    content = (ROOT / "docs/development/extensions.md").read_text()
    snippets = re.findall(r"```python\n(.*?)```", content, re.S)
    for snippet in snippets:
        exec(compile(snippet, "documented-extension", "exec"), namespace)
    # TaskRunResult.status reports storage success; task/result report action denial.
    assert namespace["result"].task.state == "FAILED"
    assert namespace["result"].task.steps[0].result.status == "denied"
    assert namespace["record"].source_id == "example.sensor" and not namespace["replayed"]
    assert namespace["reading"].value == 3
    router = namespace["router"]
    request = ModelRequest(
        request_id=uuid4(),
        session_id=app.identities.resolve_session(token).session_id,
        correlation_id=uuid4(),
        messages=(ModelMessage(role="user", content="Hello"),),
    )
    output, provenance = asyncio.run(router.complete(request, session_token=token))
    assert "Explicit example fake" in output and provenance.location == "fake"
    app.permissions.grant(
        token, app.identities.owner_id, "example.uppercase", correlation_id=uuid4()
    )
    task = app.tasks.submit(
        (
            TaskStep(
                step_id="upper", capability_id="example.uppercase", arguments={"text": "hello"}
            ),
        ),
        session_token=token,
    )
    result = app.tasks.run(task.task_id, session_token=token)
    assert result.task.state == "COMPLETED" and result.task.steps[0].result.output == {
        "text": "HELLO"
    }
    app.permissions.revoke(
        token, app.identities.owner_id, "example.uppercase", correlation_id=uuid4()
    )
    assert app.initiatives.policies(session_token=token)[0].mode == "OBSERVE_ONLY"


def test_documented_quickstart_runs_authenticated_workflow_and_actual_restart(tmp_path):
    result = run_quickstart(tmp_path / "quickstart", verbose=True)
    assert result["status"] == "ok" and result["processes"] == 2
    assert result["task"] == "COMPLETED" and result["guest_isolation"] == "verified"
    assert result["replayed_lighting_invocations"] == 0
    assert result["references"]["audit_id"]
