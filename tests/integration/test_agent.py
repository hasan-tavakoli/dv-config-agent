# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent


def test_agent_stream() -> None:
    """
    Integration test for the agent stream functionality.
    Tests that the agent returns valid streaming responses.
    """

    session_service = InMemorySessionService()

    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    message = types.Content(
        role="user", parts=[types.Part.from_text(text="Why is the sky blue?")]
    )

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )
    assert len(events) > 0, "Expected at least one message"

    has_text_content = False
    for event in events:
        if (
            event.content
            and event.content.parts
            and any(part.text for part in event.content.parts)
        ):
            has_text_content = True
            break
    assert has_text_content, "Expected at least one message with text content"


from unittest.mock import patch, MagicMock
import json

@patch("subprocess.run")
@patch("urllib.request.urlopen")
def test_agent_create_workflow(mock_urlopen, mock_run) -> None:
    """Tests the full create workflow from start to PR creation."""
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    # Mock subprocess.run output for check_config.py
    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    # Simulate stdout JSON from check_config.py
    mock_run_res.stdout = json.dumps({
        "resolved_path": "dv-stage-eu/sports/dv-sports-elt/config.json",
        "exists": "no",
        "task": "create",
        "config_path": "/tmp/config.json",
        "config_content": '{"dag_configs": []}',
        "deploy_path": "/tmp/deploy.yml",
        "deploy_content": "name: dv-sports-elt",
        "validation_passed": True,
        "validation_errors": [],
        "feature_branch": "feature/config-dv-sports-elt-12345",
        "changes": {},
        "task_needed": True
    })
    mock_run.return_value = mock_run_res

    # Mock GitHub PR response
    mock_pr_response = MagicMock()
    mock_pr_response.read.return_value = json.dumps({
        "html_url": "https://github.com/hasan-tavakoli/dv-platform-config/pull/999"
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_pr_response

    payload = {
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl",
        "tag": "feature-tag-123",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "30 0 * * *",
        "service_account": "sa@domain.com",
        "execution_project": "proj-exec",
        "target_project": "proj-target"
    }

    message = types.Content(
        role="user", parts=[types.Part.from_text(text=json.dumps(payload))]
    )

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )
    
    # Verify that the PR was successfully created and the URL is present in the events
    pr_created = False
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text and "PR URL:" in part.text:
                    pr_created = True
                    assert "https://github.com/hasan-tavakoli/dv-platform-config/pull/999" in part.text
    assert pr_created, "Expected PR URL to be logged"


@patch("subprocess.run")
def test_agent_no_change_workflow(mock_run) -> None:
    """Tests that the workflow terminates early with no_change when no changes are needed."""
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    # Mock check_config.py output showing no changes needed
    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run_res.stdout = json.dumps({
        "resolved_path": "dv-stage-eu/sports/dv-sports-elt/config.json",
        "exists": "yes",
        "task": "update",
        "config_path": "/tmp/config.json",
        "config_content": "",
        "deploy_path": "/tmp/deploy.yml",
        "deploy_content": "",
        "validation_passed": True,
        "validation_errors": [],
        "feature_branch": "",
        "changes": {},
        "task_needed": False
    })
    mock_run.return_value = mock_run_res

    payload = {
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl",
        "tag": "feature-tag-123",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "30 0 * * *",
        "service_account": "sa@domain.com",
        "execution_project": "proj-exec",
        "target_project": "proj-target"
    }

    message = types.Content(
        role="user", parts=[types.Part.from_text(text=json.dumps(payload))]
    )

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    # The flow should end with 'no changes needed' and not contain PR URL
    no_change_logged = False
    pr_created = False
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    if "no changes needed" in part.text:
                        no_change_logged = True
                    if "PR URL:" in part.text:
                        pr_created = True

    assert no_change_logged, "Expected 'no changes needed' to be logged"
    assert not pr_created, "PR should not be created when there are no changes"
