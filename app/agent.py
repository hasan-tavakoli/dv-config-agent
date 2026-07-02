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
import google.auth

from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.workflow import Workflow
from google.adk.events.event import Event
from google.adk.agents.context import Context
from google.genai import types

# Initialize Google Cloud environment
_, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

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
            match = re.search(fr'(?i)\b{key}\b\s*[:=]\s*([^\s,]+)', text)
            if match:
                params[key] = match.group(1).strip("'\"")
                
    msg = (
        f"Received config update request:\n"
        f"- Image Reference: {params.get('image_reference', 'None')}\n"
        f"- Domain: {params.get('domain', 'None')}\n"
        f"- Environment: {params.get('environment', 'None')}\n"
        f"- DAG ID: {params.get('dag_id', 'None')}"
    )
    print(msg)
    return Event(
        output=msg,
        content=types.Content(role='model', parts=[types.Part.from_text(text=msg)])
    )

root_agent = Workflow(
    name="dv_config_agent",
    edges=[
        ('START', log_input),
    ]
)

app = App(
    root_agent=root_agent,
    name="app",
)
