# ruff: noqa
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

import os
import json
import re
import subprocess
import sys
from dotenv import load_dotenv

# Load local environment variables from .env if present
load_dotenv()

# Import Step model dynamically from scripts directory
scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
from dbt_config_models import Step

from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.workflow import Workflow, node
from google.adk.events.event import Event
from google.adk.agents.context import Context
from google.adk.agents import LlmAgent
from google.genai import types
from pydantic import BaseModel, Field
from typing import Literal, Generator

# Model definition (gemini-3.1-flash-lite)
model = Gemini(
    model="gemini-3.1-flash-lite",
    retry_options=types.HttpRetryOptions(attempts=3),
)


def log_input(ctx: Context, node_input: types.Content) -> Event:
    """Extracts raw text/content and logs the received parameters."""
    text = ""
    if node_input and node_input.parts:
        text = "".join(part.text for part in node_input.parts if part.text)

    # Parse parameters (JSON or key-value/regex)
    params = {}
    try:
        params = json.loads(text)
    except Exception:
        for key in ["image_reference", "domain", "environment", "dag_id"]:
            match = re.search(rf"(?i)\b{key}\b\s*[:=]\s*([^\s,]+)", text)
            if match:
                params[key] = match.group(1).strip("'\"")

    msg = (
        f"Received config update request:\n"
        f"- Image Reference: {params.get('image_reference') or params.get('image', 'None')}\n"
        f"- Domain: {params.get('domain', 'None')}\n"
        f"- Environment: {params.get('environment', 'None')}\n"
        f"- DAG ID: {params.get('dag_id', 'None')}"
    )
    print(msg)
    return Event(
        output=msg,
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]),
        state={"payload": text},
    )


@node
def check_config_node(ctx: Context) -> Event:
    """Node that invokes check_config.py to resolve path, verify existence, and decide task type."""
    payload_str = ctx.state.get("payload", "")

    script_path = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "check_config.py"
    )

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            input=payload_str,
            capture_output=True,
            text=True,
            check=True,
        )
        output_data = json.loads(result.stdout.strip())

        resolved_path = output_data.get("resolved_path")
        exists = output_data.get("exists")
        task = output_data.get("task")

        msg = (
            f"Check Config Result:\n"
            f"- Resolved Path: {resolved_path}\n"
            f"- Exists: {exists}\n"
            f"- Task Type: {task}"
        )

        if task == "create" or (
            task == "update" and output_data.get("task_needed", True)
        ):
            config_content = output_data.get("config_content")
            deploy_content = output_data.get("deploy_content")
            config_path = output_data.get("config_path")
            deploy_path = output_data.get("deploy_path")
            label = "Generated" if task == "create" else "Updated"
            msg += (
                f"\n\n{label} config.json at {config_path}:\n"
                f"```json\n{config_content}\n```\n"
                f"\n{label} deploy.yml at {deploy_path}:\n"
                f"```yaml\n{deploy_content}\n```"
            )

        task_needed = output_data.get("task_needed", True)
        validation_passed = output_data.get("validation_passed", True)
        validation_errors = output_data.get("validation_errors", [])

        route = "ok"
        if not task_needed:
            route = "no_change"
            msg += "\n\nno changes needed"
        elif not validation_passed:
            route = "needs_human"
            errors_str = "\n".join(f"- {err}" for err in validation_errors)
            msg += f"\n\n❌ Validation Failed:\n{errors_str}"
        else:
            msg += "\n\n✅ validation passed"

        print(msg)

        return Event(
            output=output_data,
            content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]),
            state={"check_result": output_data},
            route=route,
        )
    except subprocess.CalledProcessError as e:
        err_msg = f"Error running check_config.py: {e.stderr}"
        print(err_msg, file=sys.stderr)
        return Event(
            output={"error": err_msg},
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=err_msg)]
            ),
        )


class ClassificationResult(BaseModel):
    category: Literal["STANDARD", "NON-STANDARD"] = Field(
        description="STANDARD if the normal single public-models step template fits. NON-STANDARD if the ticket implies a variation the template doesn't cover (e.g. no public step, different step name, multiple steps, etc.)."
    )
    reason: str = Field(description="Brief explanation of the classification.")


classifier_agent = LlmAgent(
    name="classifier_agent",
    model="gemini-3.1-flash-lite",
    output_schema=ClassificationResult,
    instruction=(
        "You are an expert assistant that classifies configuration intents.\n\n"
        "Here is the STANDARD steps configuration template:\n"
        "```json\n"
        "[\n"
        "  {\n"
        '    "step_name": "run_public_models",\n'
        '    "dbt_flags": {}\n'
        "  }\n"
        "]\n"
        "```\n\n"
        "Analyze the user's config_intent:\n"
        "- If it is fully consistent with this standard template (meaning it doesn't request different steps, custom flags, or omitting steps) -> category must be 'STANDARD'.\n"
        "- If it implies a deviation (e.g., omitting the public step, using a different step name, adding multiple steps, or specifying custom dbt flags) -> category must be 'NON-STANDARD'."
    ),
)


class LLMDbtFlag(BaseModel):
    flag_name: str = Field(
        description="The flag name (e.g. --select, --exclude, --vars)."
    )
    flag_value: str = Field(
        description="The value of the flag (e.g. tag:sports_daily)."
    )


class LLMStep(BaseModel):
    step_name: str = Field(
        description="The name of the dbt step (e.g., run_public_models, run_custom_models)."
    )
    task_dataset_prefix: list[str] | None = Field(
        default=None,
        description="Prefixes for dynamic task mapping; empty/absent = single task.",
    )
    dbt_invocation_command: str | None = Field(
        default=None, description="dbt command to run, e.g. build, run."
    )
    dbt_flags: list[LLMDbtFlag] | None = Field(
        default=None, description="Per-step CLI flags."
    )
    source_vars: list[str] | None = Field(
        default=None, description="Override default source variables."
    )


class CustomStepsList(BaseModel):
    steps: list[LLMStep] = Field(
        description="The list of dbt steps to run, in execution order. Must follow the Step schema."
    )


non_standard_steps_generator = LlmAgent(
    name="non_standard_steps_generator",
    model="gemini-3.1-flash-lite",
    output_schema=CustomStepsList,
    instruction=(
        "You are an expert helper that generates the steps configuration for a dbt DAG config.json based on the user's intent.\n\n"
        "Here is the STANDARD steps configuration template for reference:\n"
        "```json\n"
        "[\n"
        "  {\n"
        '    "step_name": "run_public_models",\n'
        '    "dbt_flags": {}\n'
        "  }\n"
        "]\n"
        "```\n\n"
        "Based on the user's intent, produce ONLY the steps list within the Step schema to honor the intent (e.g. omit steps, change step names, add steps, or customize flags). "
        "Each step must specify a step_name and optional parameters like task_dataset_prefix, dbt_invocation_command, or dbt_flags. "
        "Do not invent new fields outside the Step schema."
    ),
)


def classify_request(ctx: Context) -> Event:
    """Pre-classifies the request. If no config_intent is provided, defaults to standard."""
    payload_str = ctx.state.get("payload", "")
    try:
        params = json.loads(payload_str)
    except Exception:
        params = {}

    config_intent = params.get("config_intent") or ""

    if not config_intent.strip():
        return Event(
            output={"category": "STANDARD", "reason": "No config_intent provided."},
            route="standard",
        )

    return Event(output=config_intent, route="llm_classify")


def handle_classification_result(
    ctx: Context, node_input: ClassificationResult
) -> Event:
    """Handles classification output and routes to standard or non-standard steps generation."""
    category = getattr(node_input, "category", "STANDARD")
    ctx.state["request_category"] = category

    if category == "STANDARD":
        return Event(output=ctx.state.get("payload"), route="standard")
    else:
        payload_str = ctx.state.get("payload", "")
        try:
            params = json.loads(payload_str)
        except Exception:
            params = {}
        config_intent = params.get("config_intent") or ""

        prompt = (
            f"The user's config intent is classified as NON-STANDARD. Please analyze the intent and generate "
            f"the list of dbt config steps to honor it.\n\n"
            f"User Intent: {config_intent}"
        )
        return Event(output=prompt, route="non_standard")


def prepare_check_config_with_custom_steps(
    ctx: Context, node_input: CustomStepsList
) -> Event:
    """Merges custom steps into the request payload, mapping dbt_flags lists to dicts."""
    steps_list = getattr(node_input, "steps", [])

    payload_str = ctx.state.get("payload", "")
    try:
        payload = json.loads(payload_str)
    except Exception:
        payload = {}

    serialized_steps = []
    for s in steps_list:
        step_dict = s.model_dump(mode="json", exclude_none=True)
        if s.dbt_flags:
            flags_dict = {}
            for flag in s.dbt_flags:
                flags_dict[flag.flag_name] = flag.flag_value
            step_dict["dbt_flags"] = flags_dict
        serialized_steps.append(step_dict)

    payload["custom_steps"] = serialized_steps
    ctx.state["payload"] = json.dumps(payload)

    return Event(output=json.dumps(payload))


class ConfigVibeDiffSummary(BaseModel):
    plain_summary: str = Field(
        description="2-3 plain-English sentences explaining what this configuration does in the project."
    )
    risk_level: Literal["low", "medium", "high"] = Field(
        description="The risk level: low, medium, or high. Note that CREATE (a brand-new DAG) is higher risk than an image-only update."
    )
    risk_reason: str = Field(
        description="A short reason explaining the chosen risk level."
    )
    what_changed: str = Field(
        description="A bulleted description of what changed or which files were added."
    )


def prepare_pr_summarizer_input(ctx: Context, node_input: dict) -> Event:
    """Prepares prompt for the PR Vibe Diff config summarizer."""
    payload_str = ctx.state.get("payload", "")
    check_result = ctx.state.get("check_result", {})

    config_content = check_result.get("config_content", "")
    deploy_content = check_result.get("deploy_content", "")
    task = check_result.get("task", "create")
    changes = check_result.get("changes", {})
    changes_str = json.dumps(changes, indent=2)

    prompt = (
        f"Original Request Payload:\n{payload_str}\n\n"
        f"Generated config.json:\n```json\n{config_content}\n```\n\n"
        f"Generated deploy.yml:\n```yaml\n{deploy_content}\n```\n\n"
        f"Action Type: {task.upper()} (Note: CREATE means this is a brand-new DAG being added to the platform. UPDATE means we are modifying an existing DAG config.)\n\n"
        f"Changes Detected:\n```json\n{changes_str}\n```\n\n"
        f"Please analyze these inputs and generate the structured PR review summary."
    )
    return Event(output=prompt)


config_pr_summarizer = LlmAgent(
    name="config_pr_summarizer",
    model="gemini-3.1-flash-lite",
    output_schema=ConfigVibeDiffSummary,
    instruction=(
        "You are an expert code reviewer. Your job is to analyze the generated config.json and "
        "deploy.yml settings relative to the original ticket intent, and produce a structured "
        "PR review summary tailored to configuration changes. Note that a CREATE task (adding a "
        "brand-new DAG) is higher risk (typically MEDIUM or HIGH) than a simple image-only update task."
    ),
)


def create_pull_request_node(
    ctx: Context, node_input: ConfigVibeDiffSummary
) -> Generator[Event, None, None]:
    """
    Deterministic step: parses the ConfigVibeDiffSummary from the LLM, builds the markdown PR body,
    and calls the GitHub API to create a Pull Request on dv-platform-config.
    """
    import urllib.request
    import urllib.error

    plain_summary = getattr(node_input, "plain_summary", "")
    risk_level = getattr(node_input, "risk_level", "low")
    risk_reason = getattr(node_input, "risk_reason", "")
    what_changed = getattr(node_input, "what_changed", "")

    check_result = ctx.state.get("check_result", {})
    resolved_path = check_result.get("resolved_path", "")
    feature_branch = check_result.get("feature_branch", "")
    config_content = check_result.get("config_content", "")
    deploy_content = check_result.get("deploy_content", "")

    # Build PR Body in Markdown format
    pr_body = (
        f"## Summary\n"
        f"{plain_summary}\n\n"
        f"## Risk\n"
        f"- **Level**: {risk_level.upper()}\n"
        f"- **Reason**: {risk_reason}\n\n"
        f"## What Changed\n"
        f"{what_changed}\n\n"
        f"## Validation\n"
        f"- **config.json**: PASSED\n"
        f"- **deploy.yml**: PASSED\n\n"
        f"## Files changed\n"
        f"### `{resolved_path}`\n"
        f"```json\n{config_content}\n```\n\n"
        f"### `deploy.yml`\n"
        f"```yaml\n{deploy_content}\n```"
    )

    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        msg = (
            f"GITHUB_TOKEN not found in env. Simulating Pull Request creation.\n\n"
            f"=== PR Title ===\n"
            f"✨ feat: Add dbt DAG config for {resolved_path}\n\n"
            f"=== PR Base/Head ===\n"
            f"Base: main\n"
            f"Head: {feature_branch}\n\n"
            f"=== PR Body ===\n"
            f"{pr_body}"
        )
        yield Event(
            content=types.Content(role="model", parts=[types.Part.from_text(text=msg)])
        )
        yield Event(output={"pr_url": "simulated_pr_url", "pr_body": pr_body})
        return

    # Call GitHub API to create PR
    url = "https://api.github.com/repos/hasan-tavakoli/dv-platform-config/pulls"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "dv-config-agent",
        "Content-Type": "application/json",
    }
    payload = {
        "title": f"✨ feat: Add dbt DAG config for {resolved_path}",
        "head": feature_branch,
        "base": "main",
        "body": pr_body,
    }

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )

    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            pr_url = res_data.get("html_url", "")

            msg = (
                f"Successfully created Pull Request:\n"
                f"PR URL: {pr_url}\n\n"
                f"### Vibe Diff Pull Request Body\n"
                f"---\n"
                f"{pr_body}"
            )
            yield Event(
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text=msg)]
                )
            )
            yield Event(output={"pr_url": pr_url, "pr_body": pr_body})
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8") if e.fp else str(e)
        err_msg = f"Failed to create Pull Request: {e.code} {e.reason} - {err_body}"
        print(err_msg, file=sys.stderr)
        yield Event(
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=err_msg)]
            )
        )
        yield Event(output={"error": err_msg})


def handle_no_change(ctx: Context, node_input: dict) -> Event:
    """Terminal node for when no configuration changes are required."""
    msg = "No changes needed. Stopping."
    return Event(
        output={"status": "no_change", "msg": msg},
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]),
    )


def handle_needs_human(ctx: Context, node_input: dict) -> Event:
    """Terminal node for when validations or operations fail, routing to human review."""
    errors = node_input.get("validation_errors", [])
    errors_str = "\n".join(f"- {err}" for err in errors)
    msg = f"Validation failed or Git error encountered. Human review needed.\nErrors:\n{errors_str}"
    return Event(
        output={"status": "needs_human", "errors": errors},
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]),
    )


root_agent = Workflow(
    name="dv_config_agent",
    edges=[
        ("START", log_input),
        (log_input, classify_request),
        (
            classify_request,
            {"standard": check_config_node, "llm_classify": classifier_agent},
        ),
        (classifier_agent, handle_classification_result),
        (
            handle_classification_result,
            {
                "standard": check_config_node,
                "non_standard": non_standard_steps_generator,
            },
        ),
        (non_standard_steps_generator, prepare_check_config_with_custom_steps),
        (prepare_check_config_with_custom_steps, check_config_node),
        (
            check_config_node,
            {
                "ok": prepare_pr_summarizer_input,
                "no_change": handle_no_change,
                "needs_human": handle_needs_human,
            },
        ),
        (prepare_pr_summarizer_input, config_pr_summarizer),
        (config_pr_summarizer, create_pull_request_node),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)

from google.adk.a2a.utils.agent_to_a2a import to_a2a

a2a_app = to_a2a(root_agent, port=8001)
