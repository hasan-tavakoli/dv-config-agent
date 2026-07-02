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

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

# Add scripts directory to sys.path to import dbt_config_models and validate_dbt_configs
scripts_dir = Path(__file__).resolve().parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from dbt_config_models import (
    DagConfig,
    DagEntry,
    EnvVariables,
    JobConfig,
    RootConfig,
    Step,
)
from validate_dbt_configs import validate_file as validate_config_json


def load_env(env_path: Path):
    env_vars = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key.strip()] = val.strip().strip("\"'")
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

    custom_steps = payload.get("custom_steps")
    if custom_steps is not None:
        steps = [Step.model_validate(s) for s in custom_steps]
    else:
        step = Step(step_name="run_public_models", dbt_flags={})
        steps = [step]

    env_vars = EnvVariables(
        DBT_EXECUTION_PROJECT=payload.get("execution_project"),
        DBT_IMPERSONATE_SERVICE_ACCOUNT=payload.get("service_account"),
        DBT_PROJECT=payload.get("target_project"),
        DBT_PROFILE="cloud-run",
        DBT_LOCATION=location,
        DBT_DOMAIN_NAME=domain,
    )

    job_config = JobConfig(env_variables=env_vars, steps=steps)

    dag_config = DagConfig(
        dag_id=payload.get("dag_id"),
        schedule=payload.get("schedule"),
        start_date="2024-01-01",
        tags=[domain],
    )

    root_config = RootConfig(
        dag_configs=[DagEntry(dag_config=dag_config, job_config=job_config)]
    )

    return root_config.model_dump(mode="json")


def generate_deploy_yaml(payload: dict) -> str:
    dag_name = payload.get("dag_id").replace("_", "-")
    image = payload.get("image")
    tag = payload.get("tag")

    if tag:
        full_image = f"{image}:{tag}"
    else:
        full_image = f"{image}"

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


def validate_deploy_yaml(file_path: Path) -> list[str]:
    errors = []
    if not file_path.exists():
        return [f"deploy.yml does not exist at {file_path}"]

    try:
        with open(file_path) as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        return [f"deploy.yml is invalid YAML: {exc}"]

    if not isinstance(data, dict):
        return ["deploy.yml top-level element is not a dictionary"]

    required_fields = ["name", "image", "service_account", "region"]
    for field in required_fields:
        val = data.get(field)
        if not val:
            errors.append(f"deploy.yml: missing or empty required field '{field}'")
        elif not isinstance(val, str) or not val.strip():
            errors.append(f"deploy.yml: field '{field}' must be a non-empty string")

    # Check that the image contains a tag (contains ":")
    image = data.get("image")
    if image and isinstance(image, str):
        if ":" not in image:
            errors.append("deploy.yml: 'image' field must include a tag (contain ':')")
        elif image.endswith(":"):
            errors.append("deploy.yml: 'image' tag cannot be empty")

    return errors


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
    source = payload.get("source")

    if not all([environment, domain, dag_id]):
        print(
            "Error: Payload must contain environment, domain, and dag_id.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine config_only vs model update
    if source == "config_only":
        is_config_only = True
    elif source == "model":
        is_config_only = False
        if not payload.get("image") or not payload.get("tag"):
            print(
                "Error: Payload must contain image and tag when source is 'model'.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        # Fall back to old behavior (infer from image presence)
        is_config_only = payload.get("image") is None

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

    with tempfile.TemporaryDirectory(
        dir=workspace_dir, prefix="dv_platform_config_clone_"
    ) as temp_dir:
        clone_dest = Path(temp_dir)

        # Run git clone
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(clone_dest)],
                capture_output=True,
                text=True,
                check=True,
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
        config_path_str = str(target_file_path)
        deploy_path_str = str(target_file_path.parent / "deploy/deploy.yml")
        validation_passed = True
        validation_errors = []
        feature_branch = ""
        changes = {}
        task_needed = True

        if task_type == "create":
            # Create logic
            dag_dir = target_file_path.parent
            deploy_dir = dag_dir / "deploy"
            dag_dir.mkdir(parents=True, exist_ok=True)
            deploy_dir.mkdir(parents=True, exist_ok=True)

            config_data = generate_config_json(payload)
            config_content = json.dumps(config_data, indent=2)
            with open(target_file_path, "w") as f:
                f.write(config_content)

            deploy_yaml_path = deploy_dir / "deploy.yml"
            deploy_content = generate_deploy_yaml(payload)
            with open(deploy_yaml_path, "w") as f:
                f.write(deploy_content)

            # Run validations
            config_errors = validate_config_json(target_file_path)
            deploy_errors = validate_deploy_yaml(deploy_yaml_path)
            all_errors = config_errors + deploy_errors

            if all_errors:
                validation_passed = False
                validation_errors = all_errors
                print("LOG: Validation failed!", file=sys.stderr)
            else:
                validation_passed = True
                print("LOG: validation passed", file=sys.stderr)

                # Perform Git Push
                dag_name = dag_id.replace("_", "-")
                feature_branch = f"feature/config-{dag_name}-{int(time.time())}"
                try:
                    subprocess.run(
                        ["git", "checkout", "-b", feature_branch],
                        cwd=str(clone_dest),
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        ["git", "add", "."],
                        cwd=str(clone_dest),
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        [
                            "git",
                            "commit",
                            "-m",
                            f"✨ feat: Add dbt DAG config for {dag_name}",
                        ],
                        cwd=str(clone_dest),
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        [
                            "git",
                            "remote",
                            "set-url",
                            "origin",
                            f"https://x-access-token:{token}@github.com/hasan-tavakoli/dv-platform-config.git",
                        ],
                        cwd=str(clone_dest),
                        check=True,
                        capture_output=True,
                    )

                    push_res = subprocess.run(
                        ["git", "push", "origin", feature_branch],
                        cwd=str(clone_dest),
                        capture_output=True,
                        text=True,
                    )
                    if push_res.returncode != 0:
                        raise RuntimeError(
                            f"Git push failed: {push_res.stderr.replace(token, '********')}"
                        )
                    print(
                        f"LOG: Successfully pushed to {feature_branch}", file=sys.stderr
                    )
                except Exception as exc:
                    err_msg = str(exc).replace(token, "********")
                    print(f"Error during git operations: {err_msg}", file=sys.stderr)
                    validation_passed = False
                    validation_errors.append(f"Git operation failed: {err_msg}")
                    feature_branch = ""

        elif task_type == "update":
            # 1. Read existing config.json and deploy.yml
            deploy_yaml_path = Path(deploy_path_str)

            with open(target_file_path) as f:
                config_data = json.load(f)
            with open(deploy_yaml_path) as f:
                deploy_data = yaml.safe_load(f)

            dag_entry = config_data["dag_configs"][0]
            dag_config = dag_entry["dag_config"]
            job_config = dag_entry["job_config"]
            env_vars = job_config["env_variables"]

            # Helper to check, track, and update changes
            def check_and_update(field_name, current_val, new_val, update_func):
                if current_val != new_val:
                    changes[field_name] = {"old": current_val, "new": new_val}
                    update_func(new_val)

            # Compare dag_id
            check_and_update(
                "dag_id",
                dag_config.get("dag_id"),
                payload.get("dag_id"),
                lambda val: dag_config.update({"dag_id": val}),
            )
            if "dag_id" in changes:
                deploy_data["name"] = payload.get("dag_id").replace("_", "-")

            # Compare schedule
            check_and_update(
                "schedule",
                dag_config.get("schedule"),
                payload.get("schedule"),
                lambda val: dag_config.update({"schedule": val}),
            )

            # Compare service_account
            def update_sa(val):
                env_vars["DBT_IMPERSONATE_SERVICE_ACCOUNT"] = val
                deploy_data["service_account"] = val

            check_and_update(
                "service_account",
                env_vars.get("DBT_IMPERSONATE_SERVICE_ACCOUNT"),
                payload.get("service_account"),
                update_sa,
            )

            # Compare execution_project
            check_and_update(
                "execution_project",
                env_vars.get("DBT_EXECUTION_PROJECT"),
                payload.get("execution_project"),
                lambda val: env_vars.update({"DBT_EXECUTION_PROJECT": val}),
            )

            # Compare target_project
            check_and_update(
                "target_project",
                env_vars.get("DBT_PROJECT"),
                payload.get("target_project"),
                lambda val: env_vars.update({"DBT_PROJECT": val}),
            )

            # Compare image
            if not is_config_only:
                new_image = (
                    f"{payload.get('image')}:{payload.get('tag')}"
                    if payload.get("tag")
                    else payload.get("image")
                )
                check_and_update(
                    "image",
                    deploy_data.get("image"),
                    new_image,
                    lambda val: deploy_data.update({"image": val}),
                )

            # Compare domain
            def update_domain(val):
                env_vars["DBT_DOMAIN_NAME"] = val
                old_dom = changes.get("domain", {}).get("old", "")
                tags = dag_config.get("tags", [])
                if old_dom in tags:
                    tags[tags.index(old_dom)] = val
                else:
                    tags.append(val)
                dag_config["tags"] = tags

            check_and_update(
                "domain",
                env_vars.get("DBT_DOMAIN_NAME"),
                payload.get("domain"),
                update_domain,
            )

            # Compare environment / region
            region = (
                "southamerica-east1"
                if "sa" in payload.get("environment", "")
                else "europe-west1"
            )

            def update_region(val):
                deploy_data["region"] = val
                env_vars["DBT_LOCATION"] = val

            check_and_update("region", deploy_data.get("region"), region, update_region)

            # Compare custom_steps if provided
            custom_steps = payload.get("custom_steps")
            if custom_steps is not None:
                new_steps_list = [
                    Step.model_validate(s).model_dump(mode="json") for s in custom_steps
                ]
                current_steps_list = [
                    Step.model_validate(s).model_dump(mode="json")
                    for s in job_config.get("steps", [])
                ]

                if current_steps_list != new_steps_list:
                    changes["steps"] = {
                        "old": current_steps_list,
                        "new": new_steps_list,
                    }
                    job_config["steps"] = new_steps_list

            if not changes:
                print("LOG: no changes needed", file=sys.stderr)
                task_needed = False
            else:
                # Write changes back to file
                config_content = json.dumps(config_data, indent=2)
                with open(target_file_path, "w") as f:
                    f.write(config_content)

                # Format yaml properly
                deploy_content = yaml.dump(
                    deploy_data, default_flow_style=False, sort_keys=False
                )
                with open(deploy_yaml_path, "w") as f:
                    f.write(deploy_content)

                # Run validations
                config_errors = validate_config_json(target_file_path)
                deploy_errors = validate_deploy_yaml(deploy_yaml_path)
                all_errors = config_errors + deploy_errors

                if all_errors:
                    validation_passed = False
                    validation_errors = all_errors
                    print("LOG: Validation failed!", file=sys.stderr)
                else:
                    validation_passed = True
                    print("LOG: validation passed", file=sys.stderr)

                    # Perform Git Push for Update
                    dag_name = dag_id.replace("_", "-")
                    feature_branch = (
                        f"feature/config-update-{dag_name}-{int(time.time())}"
                    )
                    try:
                        subprocess.run(
                            ["git", "checkout", "-b", feature_branch],
                            cwd=str(clone_dest),
                            check=True,
                            capture_output=True,
                        )
                        subprocess.run(
                            ["git", "add", "."],
                            cwd=str(clone_dest),
                            check=True,
                            capture_output=True,
                        )
                        subprocess.run(
                            [
                                "git",
                                "commit",
                                "-m",
                                f"🔧 update: Update config for {dag_name}",
                            ],
                            cwd=str(clone_dest),
                            check=True,
                            capture_output=True,
                        )
                        subprocess.run(
                            [
                                "git",
                                "remote",
                                "set-url",
                                "origin",
                                f"https://x-access-token:{token}@github.com/hasan-tavakoli/dv-platform-config.git",
                            ],
                            cwd=str(clone_dest),
                            check=True,
                            capture_output=True,
                        )

                        push_res = subprocess.run(
                            ["git", "push", "origin", feature_branch],
                            cwd=str(clone_dest),
                            capture_output=True,
                            text=True,
                        )
                        if push_res.returncode != 0:
                            raise RuntimeError(
                                f"Git push failed: {push_res.stderr.replace(token, '********')}"
                            )
                        print(
                            f"LOG: Successfully pushed to {feature_branch}",
                            file=sys.stderr,
                        )
                    except Exception as exc:
                        err_msg = str(exc).replace(token, "********")
                        print(
                            f"Error during git operations: {err_msg}", file=sys.stderr
                        )
                        validation_passed = False
                        validation_errors.append(f"Git operation failed: {err_msg}")
                        feature_branch = ""

            # Log print details
            if task_needed:
                print(f"LOG: Updated config.json at {rel_path}", file=sys.stderr)
                print(
                    f"LOG: Updated deploy.yml at {deploy_yaml_path.relative_to(clone_dest)}",
                    file=sys.stderr,
                )
                print(f"\n--- GENERATED config.json ({rel_path}) ---", file=sys.stderr)
                print(config_content, file=sys.stderr)
                print(
                    f"\n--- GENERATED deploy.yml ({deploy_yaml_path.relative_to(clone_dest)}) ---",
                    file=sys.stderr,
                )
                print(deploy_content, file=sys.stderr)
                print("-------------------------------------------\n", file=sys.stderr)

        result = {
            "resolved_path": rel_path,
            "exists": "yes" if exists else "no",
            "task": task_type,
            "config_path": config_path_str,
            "config_content": config_content,
            "deploy_path": deploy_path_str,
            "deploy_content": deploy_content,
            "validation_passed": validation_passed,
            "validation_errors": validation_errors,
            "feature_branch": feature_branch,
            "changes": changes,
            "task_needed": task_needed,
        }

    # Output to stdout as JSON
    print(json.dumps(result))

    # Log decided task type to stderr
    print(f"LOG: Decided task type: {task_type}", file=sys.stderr)


if __name__ == "__main__":
    main()
