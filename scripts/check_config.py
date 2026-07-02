#!/usr/bin/env python3
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
import sys
import json
import tempfile
import subprocess
from pathlib import Path

# Add scripts directory to sys.path to import dbt_config_models
scripts_dir = Path(__file__).resolve().parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from dbt_config_models import RootConfig, DagEntry, DagConfig, JobConfig, EnvVariables, Step

def load_env(env_path: Path):
    env_vars = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    env_vars[key.strip()] = val.strip().strip('"\'')
    return env_vars

def get_github_token() -> str:
    # Check OS env first
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
        
    # Check .env in cwd and script parent dir
    search_dirs = [Path.cwd(), Path(__file__).resolve().parents[1]]
    for d in search_dirs:
        env_file = d / ".env"
        if env_file.exists():
            env_vars = load_env(env_file)
            if "GITHUB_TOKEN" in env_vars:
                return env_vars["GITHUB_TOKEN"]
                
    raise ValueError("GITHUB_TOKEN not found in .env or environment variables.")

def resolve_path(environment: str, domain: str, dag_id: str) -> str:
    env_map = {
        "dev": "dv-dev-eu",
        "stage": "dv-stage-eu",
        "stage-sa": "dv-stage-sa",
    }
    subtree = env_map.get(environment)
    if not subtree:
        if environment.startswith("dv-"):
            subtree = environment
        else:
            raise ValueError(f"Unknown environment: {environment}")
    
    # Replace underscores in dag_id with hyphens
    dag_name = dag_id.replace("_", "-")
    return f"{subtree}/{domain}/{dag_name}/config.json"

def generate_config_json(payload: dict) -> dict:
    domain = payload.get("domain")
    environment = payload.get("environment", "")
    location = "southamerica-east1" if "sa" in environment else "europe-west1"
    
    step = Step(
        step_name="run_public_models",
        dbt_flags={}
    )
    
    env_vars = EnvVariables(
        DBT_EXECUTION_PROJECT=payload.get("execution_project"),
        DBT_IMPERSONATE_SERVICE_ACCOUNT=payload.get("service_account"),
        DBT_PROJECT=payload.get("target_project"),
        DBT_PROFILE="cloud-run",
        DBT_LOCATION=location,
        DBT_DOMAIN_NAME=domain
    )
    
    job_config = JobConfig(
        env_variables=env_vars,
        steps=[step]
    )
    
    dag_config = DagConfig(
        dag_id=payload.get("dag_id"),
        schedule=payload.get("schedule"),
        start_date="2024-01-01",
        tags=[domain]
    )
    
    root_config = RootConfig(
        dag_configs=[
            DagEntry(
                dag_config=dag_config,
                job_config=job_config
            )
        ]
    )
    
    return root_config.model_dump(mode="json")

def generate_deploy_yaml(payload: dict) -> str:
    dag_name = payload.get("dag_id").replace("_", "-")
    image = payload.get("image")
    tag = payload.get("tag")
    full_image = f"{image}:{tag}"
    service_account = payload.get("service_account")
    environment = payload.get("environment", "")
    region = "southamerica-east1" if "sa" in environment else "europe-west1"
    
    return f"""name: {dag_name}
image: {full_image}
service_account: {service_account}
region: {region}
resources:
  limits:
    cpu: 1000m
    memory: 2Gi
  requests:
    cpu: 500m
    memory: 1Gi
"""

def main():
    # Read payload from argument or stdin
    payload_str = None
    if len(sys.argv) > 1:
        payload_str = sys.argv[1]
    else:
        # Check if stdin is not a TTY
        if not sys.stdin.isatty():
            payload_str = sys.stdin.read()
            
    if not payload_str:
        print("Error: No JSON payload provided.", file=sys.stderr)
        sys.exit(1)
        
    try:
        payload = json.loads(payload_str.strip())
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON payload: {e}", file=sys.stderr)
        sys.exit(1)
        
    # Extract needed fields
    environment = payload.get("environment")
    domain = payload.get("domain")
    dag_id = payload.get("dag_id")
    
    if not all([environment, domain, dag_id]):
        print("Error: Payload must contain environment, domain, and dag_id.", file=sys.stderr)
        sys.exit(1)
        
    # 1. Resolve path
    try:
        rel_path = resolve_path(environment, domain, dag_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
        
    # 2. Clone repo and check existence
    try:
        token = get_github_token()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
        
    repo_url = f"https://{token}@github.com/hasan-tavakoli/dv-platform-config.git"
    
    # Clone to a temporary directory inside the workspace
    workspace_dir = Path.cwd()
    
    with tempfile.TemporaryDirectory(dir=workspace_dir, prefix="dv_platform_config_clone_") as temp_dir:
        clone_dest = Path(temp_dir)
        
        # Run git clone
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(clone_dest)],
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as e:
            # Mask token in error output if present
            err_msg = e.stderr.replace(token, "********")
            print(f"Error cloning repository: {err_msg}", file=sys.stderr)
            sys.exit(1)
            
        # Check if config.json exists
        target_file_path = clone_dest / rel_path
        exists = target_file_path.exists()
        task_type = "update" if exists else "create"
        
        config_content = ""
        deploy_content = ""
        config_path_str = ""
        deploy_path_str = ""
        
        if task_type == "create":
            # 1. Create target directory and deploy directory
            dag_dir = target_file_path.parent
            deploy_dir = dag_dir / "deploy"
            dag_dir.mkdir(parents=True, exist_ok=True)
            deploy_dir.mkdir(parents=True, exist_ok=True)
            
            # 2. Generate and write config.json
            config_data = generate_config_json(payload)
            config_content = json.dumps(config_data, indent=2)
            with open(target_file_path, "w") as f:
                f.write(config_content)
            config_path_str = str(target_file_path)
            
            # 3. Generate and write deploy.yml
            deploy_yaml_path = deploy_dir / "deploy.yml"
            deploy_content = generate_deploy_yaml(payload)
            with open(deploy_yaml_path, "w") as f:
                f.write(deploy_content)
            deploy_path_str = str(deploy_yaml_path)
            
            # Print log messages to stderr
            print(f"LOG: Created config.json at {rel_path}", file=sys.stderr)
            print(f"LOG: Created deploy.yml at {dag_dir.relative_to(clone_dest)}/deploy/deploy.yml", file=sys.stderr)
            print(f"\n--- GENERATED config.json ({rel_path}) ---", file=sys.stderr)
            print(config_content, file=sys.stderr)
            print(f"\n--- GENERATED deploy.yml ({dag_dir.relative_to(clone_dest)}/deploy/deploy.yml) ---", file=sys.stderr)
            print(deploy_content, file=sys.stderr)
            print("-------------------------------------------\n", file=sys.stderr)
        
        result = {
            "resolved_path": rel_path,
            "exists": "yes" if exists else "no",
            "task": task_type,
            "config_path": config_path_str,
            "config_content": config_content,
            "deploy_path": deploy_path_str,
            "deploy_content": deploy_content
        }
        
    # Output to stdout as JSON
    print(json.dumps(result))
    
    # Log decided task type to stderr
    print(f"LOG: Decided task type: {task_type}", file=sys.stderr)

if __name__ == "__main__":
    main()
