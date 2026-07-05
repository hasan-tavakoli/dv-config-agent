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
"""
Covers app.agent._build_pr_title:
- config PRs originating from the model path (source == "model") should be
  visually distinguishable from plain config_only PRs, using only data
  already available to the config-agent (source, dag_id).
- the conventional-commit prefix/verb should reflect create vs update
  (check_config.py's check_result["task"]), defaulting to create wording
  when that signal is missing or unrecognized.

Also covers app.agent.prepare_pr_summarizer_input: the Vibe Diff summarizer
LLM was writing the wrong domain (inferred from the shared "dv-sports-etl"
image repo name) and inventing "production" out of thin air. The fix states
the real domain/environment prominently and verbatim at the top of the
prompt, with an explicit instruction not to infer either from the image name.
"""

import json
from unittest.mock import MagicMock

from google.adk.agents.context import Context

from app.agent import _build_pr_title, prepare_pr_summarizer_input


def test_model_source_prefixes_title_with_dag_id():
    payload_str = json.dumps({"source": "model", "dag_id": "dv_sports_elt"})

    title = _build_pr_title("dv-dev-eu/sports/dv-sports-elt/config.json", payload_str)

    assert title == "✨ feat: [model] dv_sports_elt — Add dbt DAG config for dv-dev-eu/sports/dv-sports-elt/config.json"


def test_config_only_source_keeps_title_unchanged():
    payload_str = json.dumps({"source": "config_only", "dag_id": "dv_sports_elt"})

    title = _build_pr_title("dv-dev-eu/sports/dv-sports-elt/config.json", payload_str)

    assert title == "✨ feat: Add dbt DAG config for dv-dev-eu/sports/dv-sports-elt/config.json"


def test_missing_source_keeps_title_unchanged():
    payload_str = json.dumps({"dag_id": "dv_sports_elt"})

    title = _build_pr_title("dv-dev-eu/sports/dv-sports-elt/config.json", payload_str)

    assert title == "✨ feat: Add dbt DAG config for dv-dev-eu/sports/dv-sports-elt/config.json"


def test_model_source_with_missing_dag_id_falls_back_to_bare_model_tag():
    payload_str = json.dumps({"source": "model"})

    title = _build_pr_title("dv-dev-eu/sports/dv-sports-elt/config.json", payload_str)

    assert title == "✨ feat: [model] — Add dbt DAG config for dv-dev-eu/sports/dv-sports-elt/config.json"


def test_unparseable_payload_never_raises_and_keeps_title_unchanged():
    title = _build_pr_title("dv-dev-eu/sports/dv-sports-elt/config.json", "not valid json")

    assert title == "✨ feat: Add dbt DAG config for dv-dev-eu/sports/dv-sports-elt/config.json"


def test_empty_payload_string_keeps_title_unchanged():
    title = _build_pr_title("dv-dev-eu/sports/dv-sports-elt/config.json", "")

    assert title == "✨ feat: Add dbt DAG config for dv-dev-eu/sports/dv-sports-elt/config.json"


def test_update_model_title_uses_refactor_prefix_and_update_verb():
    payload_str = json.dumps({"source": "model", "dag_id": "dv_sports_elt"})

    title = _build_pr_title("dv-dev-eu/sports/dv-sports-elt/config.json", payload_str, task="update")

    assert title == "♻️ refactor: [model] dv_sports_elt — Update dbt DAG config for dv-dev-eu/sports/dv-sports-elt/config.json"


def test_update_config_only_title_uses_refactor_prefix_and_update_verb():
    payload_str = json.dumps({"source": "config_only", "dag_id": "dv_sports_elt"})

    title = _build_pr_title("dv-dev-eu/sports/dv-sports-elt/config.json", payload_str, task="update")

    assert title == "♻️ refactor: Update dbt DAG config for dv-dev-eu/sports/dv-sports-elt/config.json"


def test_none_task_value_defaults_to_create_wording():
    # check_result.get("task", "create") could still yield None if the key
    # exists but is explicitly None - must not be treated as "update".
    payload_str = json.dumps({"source": "model", "dag_id": "dv_sports_elt"})

    title = _build_pr_title("dv-dev-eu/sports/dv-sports-elt/config.json", payload_str, task=None)

    assert title == "✨ feat: [model] dv_sports_elt — Add dbt DAG config for dv-dev-eu/sports/dv-sports-elt/config.json"


def test_unrecognized_task_value_defaults_to_create_wording():
    payload_str = json.dumps({"source": "config_only"})

    title = _build_pr_title("dv-dev-eu/sports/dv-sports-elt/config.json", payload_str, task="unknown_status")

    assert title == "✨ feat: Add dbt DAG config for dv-dev-eu/sports/dv-sports-elt/config.json"


def test_prepare_pr_summarizer_input_states_actual_domain_and_environment_prominently():
    # Regression test: the summarizer used to infer "sports" from the shared
    # "dv-sports-etl" image repo name (present in deploy_content below) even
    # when the real domain was "analytics". The prompt must state the real
    # domain/environment explicitly, and warn against inferring from the image.
    ctx = MagicMock(spec=Context)
    ctx.state = {
        "payload": json.dumps({
            "source": "model",
            "domain": "analytics",
            "environment": "stage",
            "dag_id": "dv_analytics_elt",
        }),
        "check_result": {
            "config_content": "{}",
            "deploy_content": "image: ghcr.io/hasan-tavakoli/dv-sports-etl:main-123",
            "task": "create",
            "changes": {},
        },
    }

    event = prepare_pr_summarizer_input(ctx, {})

    assert "Domain: analytics" in event.output
    assert "Environment: stage" in event.output
    assert "do not infer the domain from the container image name" in event.output


def test_prepare_pr_summarizer_input_handles_unparseable_payload_without_raising():
    ctx = MagicMock(spec=Context)
    ctx.state = {"payload": "not valid json", "check_result": {}}

    event = prepare_pr_summarizer_input(ctx, {})

    assert "Domain: unknown" in event.output
    assert "Environment: unknown" in event.output
