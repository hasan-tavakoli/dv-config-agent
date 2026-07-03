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
from unittest.mock import MagicMock, patch

import pytest
import yaml

from scripts.check_config import main, resolve_path


def test_resolve_path():
    # Test valid dev mapping
    path = resolve_path("dev", "sports", "dv_sports_elt")
    assert path == "dv-dev-eu/sports/dv-sports-elt/config.json"

    # Test valid stage mapping
    path = resolve_path("stage", "wallet", "dv_wallet_elt")
    assert path == "dv-stage-eu/wallet/dv-wallet-elt/config.json"

    # Test valid stage-sa mapping
    path = resolve_path("stage-sa", "analytics", "dv_analytics_elt")
    assert path == "dv-stage-sa/analytics/dv-analytics-elt/config.json"

    # Test raw dv prefix environment
    path = resolve_path("dv-custom-env", "bi", "dv_bi_elt")
    assert path == "dv-custom-env/bi/dv-bi-elt/config.json"

    # Test invalid environment
    with pytest.raises(ValueError, match="Unknown environment"):
        resolve_path("invalid-env", "sports", "dv_sports_elt")


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_exists(mock_temp_dir, mock_get_token, mock_run, tmp_path):
    mock_get_token.return_value = "dummy-token"

    # Mock TemporaryDirectory context manager
    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = str(tmp_path)
    mock_temp_dir.return_value = mock_temp_dir_instance

    # Mock subprocess.run for git commands
    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run_res.stdout = ""
    mock_run_res.stderr = ""
    mock_run.return_value = mock_run_res

    # Write dummy files to tmp_path
    rel_path = "dv-stage-eu/sports/dv-sports-elt/config.json"
    target_config = tmp_path / rel_path
    target_deploy = tmp_path / "dv-stage-eu/sports/dv-sports-elt/deploy/deploy.yml"
    target_config.parent.mkdir(parents=True, exist_ok=True)
    (target_config.parent / "deploy").mkdir(parents=True, exist_ok=True)

    dummy_config = {
        "dag_configs": [
            {
                "dag_config": {
                    "dag_id": "dv_sports_elt",
                    "schedule": "30 0 * * *",
                    "start_date": "2024-01-01",
                    "tags": ["sports"],
                },
                "job_config": {
                    "env_variables": {
                        "DBT_EXECUTION_PROJECT": "dv-dev-eu-w1-sports-elt",
                        "DBT_IMPERSONATE_SERVICE_ACCOUNT": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
                        "DBT_PROJECT": "dv-dev-eu-w1-sports-data",
                        "DBT_PROFILE": "cloud-run",
                        "DBT_LOCATION": "europe-west1",
                        "DBT_DOMAIN_NAME": "sports",
                    },
                    "steps": [{"step_name": "run_public_models"}],
                },
            }
        ]
    }
    with open(target_config, "w") as f:
        json.dump(dummy_config, f)

    dummy_deploy = {
        "name": "dv-sports-elt",
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl:old-tag",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "region": "europe-west1",
    }
    with open(target_deploy, "w") as f:
        yaml.safe_dump(dummy_deploy, f)

    # 1. First test: payload matches existing values (no changes needed)
    payload = {
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl",
        "tag": "old-tag",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "30 0 * * *",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data",
    }

    with patch("sys.argv", ["check_config.py", json.dumps(payload)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        # Check output JSON
        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None, (
            f"Could not find valid JSON in printed lines: {printed_lines}"
        )
        assert (
            output_json["resolved_path"]
            == "dv-stage-eu/sports/dv-sports-elt/config.json"
        )
        assert output_json["exists"] == "yes"
        assert output_json["task"] == "update"
        assert output_json["task_needed"] is False

    # 2. Second test: payload has changes
    payload_changes = payload.copy()
    payload_changes["tag"] = "new-tag"
    payload_changes["schedule"] = "0 1 * * *"

    with patch("sys.argv", ["check_config.py", json.dumps(payload_changes)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        # Check output JSON
        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None, (
            f"Could not find valid JSON in printed lines: {printed_lines}"
        )
        assert output_json["task_needed"] is True
        assert output_json["changes"]["image"] == {
            "old": "ghcr.io/hasan-tavakoli/dv-sports-etl:old-tag",
            "new": "ghcr.io/hasan-tavakoli/dv-sports-etl:new-tag",
        }
        assert output_json["changes"]["schedule"] == {
            "old": "30 0 * * *",
            "new": "0 1 * * *",
        }


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_create(mock_temp_dir, mock_get_token, mock_run, tmp_path):
    mock_get_token.return_value = "dummy-token"

    # Mock TemporaryDirectory context manager to point to our test tmp_path
    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = str(tmp_path)
    mock_temp_dir.return_value = mock_temp_dir_instance

    # Mock subprocess.run for git commands
    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run_res.stdout = ""
    mock_run_res.stderr = ""
    mock_run.return_value = mock_run_res

    payload = {
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl",
        "tag": "feature-add-model-daily_active_customers-1782992764-24fc831",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "30 0 * * *",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data",
    }

    # Since exists will return False (because tmp_path is empty), it should enter the create branch
    with patch("sys.argv", ["check_config.py", json.dumps(payload)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        # Check output JSON
        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None, (
            f"Could not find valid JSON in printed lines: {printed_lines}"
        )
        assert (
            output_json["resolved_path"]
            == "dv-stage-eu/sports/dv-sports-elt/config.json"
        )
        assert output_json["exists"] == "no"
        assert output_json["task"] == "create"

        # Verify created files exist and check content
        created_config_path = tmp_path / "dv-stage-eu/sports/dv-sports-elt/config.json"
        created_deploy_path = (
            tmp_path / "dv-stage-eu/sports/dv-sports-elt/deploy/deploy.yml"
        )

        assert created_config_path.exists()
        assert created_deploy_path.exists()

        # Read and check config.json content
        with open(created_config_path) as f:
            config_data = json.load(f)

        assert config_data["dag_configs"][0]["dag_config"]["dag_id"] == "dv_sports_elt"
        assert config_data["dag_configs"][0]["dag_config"]["schedule"] == "30 0 * * *"
        assert (
            config_data["dag_configs"][0]["job_config"]["env_variables"][
                "DBT_EXECUTION_PROJECT"
            ]
            == "dv-dev-eu-w1-sports-elt"
        )
        assert (
            config_data["dag_configs"][0]["job_config"]["env_variables"]["DBT_PROJECT"]
            == "dv-dev-eu-w1-sports-data"
        )
        assert (
            config_data["dag_configs"][0]["job_config"]["env_variables"]["DBT_LOCATION"]
            == "europe-west1"
        )

        # Read and check deploy.yml content
        with open(created_deploy_path) as f:
            deploy_text = f.read()

        assert "name: dv-sports-elt" in deploy_text
        assert (
            "image: ghcr.io/hasan-tavakoli/dv-sports-etl:feature-add-model-daily_active_customers-1782992764-24fc831"
            in deploy_text
        )
        assert "region: europe-west1" in deploy_text

        assert output_json["validation_passed"] is True
        assert len(output_json["validation_errors"]) == 0


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_validation_fails(mock_temp_dir, mock_get_token, mock_run, tmp_path):
    mock_get_token.return_value = "dummy-token"

    # Mock TemporaryDirectory context manager to point to our test tmp_path
    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = str(tmp_path)
    mock_temp_dir.return_value = mock_temp_dir_instance

    # Mock subprocess.run for git commands
    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run_res.stdout = ""
    mock_run_res.stderr = ""
    mock_run.return_value = mock_run_res

    payload = {
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl",
        "tag": "",  # Empty tag to trigger validation error
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "30 0 * * *",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data",
    }

    with patch("sys.argv", ["check_config.py", json.dumps(payload)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        # Check output JSON
        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None, (
            f"Could not find valid JSON in printed lines: {printed_lines}"
        )
        assert output_json["validation_passed"] is False
        assert any(
            "deploy.yml: 'image' field must include a tag" in err
            for err in output_json["validation_errors"]
        )


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_config_only_update(mock_temp_dir, mock_get_token, mock_run, tmp_path):
    mock_get_token.return_value = "dummy-token"

    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = str(tmp_path)
    mock_temp_dir.return_value = mock_temp_dir_instance

    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run.return_value = mock_run_res

    # Write dummy files to tmp_path
    rel_path = "dv-stage-eu/sports/dv-sports-elt/config.json"
    target_config = tmp_path / rel_path
    target_deploy = tmp_path / "dv-stage-eu/sports/dv-sports-elt/deploy/deploy.yml"
    target_config.parent.mkdir(parents=True, exist_ok=True)
    (target_config.parent / "deploy").mkdir(parents=True, exist_ok=True)

    dummy_config = {
        "dag_configs": [
            {
                "dag_config": {
                    "dag_id": "dv_sports_elt",
                    "schedule": "30 0 * * *",
                    "start_date": "2024-01-01",
                    "tags": ["sports"],
                },
                "job_config": {
                    "env_variables": {
                        "DBT_EXECUTION_PROJECT": "dv-dev-eu-w1-sports-elt",
                        "DBT_IMPERSONATE_SERVICE_ACCOUNT": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
                        "DBT_PROJECT": "dv-dev-eu-w1-sports-data",
                        "DBT_PROFILE": "cloud-run",
                        "DBT_LOCATION": "europe-west1",
                        "DBT_DOMAIN_NAME": "sports",
                    },
                    "steps": [{"step_name": "run_public_models"}],
                },
            }
        ]
    }
    with open(target_config, "w") as f:
        json.dump(dummy_config, f)

    dummy_deploy = {
        "name": "dv-sports-elt",
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl:old-tag",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "region": "europe-west1",
    }
    with open(target_deploy, "w") as f:
        yaml.safe_dump(dummy_deploy, f)

    # Config only payload (no image/tag, source: "config_only")
    payload = {
        "source": "config_only",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "0 2 * * *",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data",
    }

    with patch("sys.argv", ["check_config.py", json.dumps(payload)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None
        assert output_json["task_needed"] is True
        assert "schedule" in output_json["changes"]
        assert "image" not in output_json["changes"]

        # Verify deploy.yml was NOT updated to write image: null or overwrite existing image
        with open(target_deploy) as f:
            updated_deploy = yaml.safe_load(f)
        assert updated_deploy["image"] == "ghcr.io/hasan-tavakoli/dv-sports-etl:old-tag"


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_model_update(mock_temp_dir, mock_get_token, mock_run, tmp_path):
    mock_get_token.return_value = "dummy-token"

    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = str(tmp_path)
    mock_temp_dir.return_value = mock_temp_dir_instance

    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run.return_value = mock_run_res

    rel_path = "dv-stage-eu/sports/dv-sports-elt/config.json"
    target_config = tmp_path / rel_path
    target_deploy = tmp_path / "dv-stage-eu/sports/dv-sports-elt/deploy/deploy.yml"
    target_config.parent.mkdir(parents=True, exist_ok=True)
    (target_config.parent / "deploy").mkdir(parents=True, exist_ok=True)

    dummy_config = {
        "dag_configs": [
            {
                "dag_config": {
                    "dag_id": "dv_sports_elt",
                    "schedule": "30 0 * * *",
                    "start_date": "2024-01-01",
                    "tags": ["sports"],
                },
                "job_config": {
                    "env_variables": {
                        "DBT_EXECUTION_PROJECT": "dv-dev-eu-w1-sports-elt",
                        "DBT_IMPERSONATE_SERVICE_ACCOUNT": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
                        "DBT_PROJECT": "dv-dev-eu-w1-sports-data",
                        "DBT_PROFILE": "cloud-run",
                        "DBT_LOCATION": "europe-west1",
                        "DBT_DOMAIN_NAME": "sports",
                    },
                    "steps": [{"step_name": "run_public_models"}],
                },
            }
        ]
    }
    with open(target_config, "w") as f:
        json.dump(dummy_config, f)

    dummy_deploy = {
        "name": "dv-sports-elt",
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl:old-tag",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "region": "europe-west1",
    }
    with open(target_deploy, "w") as f:
        yaml.safe_dump(dummy_deploy, f)

    # Model payload (has source: "model", plus image and tag)
    payload = {
        "source": "model",
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl",
        "tag": "new-tag",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "30 0 * * *",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data",
    }

    with patch("sys.argv", ["check_config.py", json.dumps(payload)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None
        assert output_json["task_needed"] is True
        assert "image" in output_json["changes"]
        assert output_json["changes"]["image"] == {
            "old": "ghcr.io/hasan-tavakoli/dv-sports-etl:old-tag",
            "new": "ghcr.io/hasan-tavakoli/dv-sports-etl:new-tag",
        }


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_model_validation_fails(mock_temp_dir, mock_get_token, mock_run, tmp_path):
    mock_get_token.return_value = "dummy-token"

    # Missing image/tag on model source should exit
    payload = {
        "source": "model",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "30 0 * * *",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data",
    }

    with patch("sys.argv", ["check_config.py", json.dumps(payload)]):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 1


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_source_missing_fallback(
    mock_temp_dir, mock_get_token, mock_run, tmp_path
):
    mock_get_token.return_value = "dummy-token"

    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = str(tmp_path)
    mock_temp_dir.return_value = mock_temp_dir_instance

    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run.return_value = mock_run_res

    rel_path = "dv-stage-eu/sports/dv-sports-elt/config.json"
    target_config = tmp_path / rel_path
    target_deploy = tmp_path / "dv-stage-eu/sports/dv-sports-elt/deploy/deploy.yml"
    target_config.parent.mkdir(parents=True, exist_ok=True)
    (target_config.parent / "deploy").mkdir(parents=True, exist_ok=True)

    dummy_config = {
        "dag_configs": [
            {
                "dag_config": {
                    "dag_id": "dv_sports_elt",
                    "schedule": "30 0 * * *",
                    "start_date": "2024-01-01",
                    "tags": ["sports"],
                },
                "job_config": {
                    "env_variables": {
                        "DBT_EXECUTION_PROJECT": "dv-dev-eu-w1-sports-elt",
                        "DBT_IMPERSONATE_SERVICE_ACCOUNT": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
                        "DBT_PROJECT": "dv-dev-eu-w1-sports-data",
                        "DBT_PROFILE": "cloud-run",
                        "DBT_LOCATION": "europe-west1",
                        "DBT_DOMAIN_NAME": "sports",
                    },
                    "steps": [{"step_name": "run_public_models"}],
                },
            }
        ]
    }
    with open(target_config, "w") as f:
        json.dump(dummy_config, f)

    dummy_deploy = {
        "name": "dv-sports-elt",
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl:old-tag",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "region": "europe-west1",
    }
    with open(target_deploy, "w") as f:
        yaml.safe_dump(dummy_deploy, f)

    # 1. No source, image is missing -> behaves as config_only (leaves image untouched)
    payload_no_image = {
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "0 2 * * *",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data",
    }

    with patch("sys.argv", ["check_config.py", json.dumps(payload_no_image)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None
        assert output_json["task_needed"] is True
        assert "schedule" in output_json["changes"]
        assert "image" not in output_json["changes"]

    # 2. No source, image is present -> behaves as model (updates image)
    payload_with_image = payload_no_image.copy()
    payload_with_image["image"] = "ghcr.io/hasan-tavakoli/dv-sports-etl"
    payload_with_image["tag"] = "new-tag"

    with patch("sys.argv", ["check_config.py", json.dumps(payload_with_image)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None
        assert "image" in output_json["changes"]
        assert output_json["changes"]["image"] == {
            "old": "ghcr.io/hasan-tavakoli/dv-sports-etl:old-tag",
            "new": "ghcr.io/hasan-tavakoli/dv-sports-etl:new-tag",
        }


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_update_preserves_schedule_if_empty(
    mock_temp_dir, mock_get_token, mock_run, tmp_path
):
    mock_get_token.return_value = "dummy-token"

    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = str(tmp_path)
    mock_temp_dir.return_value = mock_temp_dir_instance

    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run.return_value = mock_run_res

    # Write dummy files to tmp_path
    rel_path = "dv-stage-eu/sports/dv-sports-elt/config.json"
    target_config = tmp_path / rel_path
    target_deploy = tmp_path / "dv-stage-eu/sports/dv-sports-elt/deploy/deploy.yml"
    target_config.parent.mkdir(parents=True, exist_ok=True)
    (target_config.parent / "deploy").mkdir(parents=True, exist_ok=True)

    dummy_config = {
        "dag_configs": [
            {
                "dag_config": {
                    "dag_id": "dv_sports_elt",
                    "schedule": "30 0 * * *",
                    "start_date": "2024-01-01",
                    "tags": ["sports"],
                },
                "job_config": {
                    "env_variables": {
                        "DBT_EXECUTION_PROJECT": "dv-dev-eu-w1-sports-elt",
                        "DBT_IMPERSONATE_SERVICE_ACCOUNT": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
                        "DBT_PROJECT": "dv-dev-eu-w1-sports-data",
                        "DBT_PROFILE": "cloud-run",
                        "DBT_LOCATION": "europe-west1",
                        "DBT_DOMAIN_NAME": "sports",
                    },
                    "steps": [{"step_name": "run_public_models"}],
                },
            }
        ]
    }
    with open(target_config, "w") as f:
        json.dump(dummy_config, f)

    dummy_deploy = {
        "name": "dv-sports-elt",
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl:old-tag",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "region": "europe-west1",
    }
    with open(target_deploy, "w") as f:
        yaml.safe_dump(dummy_deploy, f)

    # Model payload with empty schedule
    payload = {
        "source": "model",
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl",
        "tag": "new-tag",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data",
    }

    with patch("sys.argv", ["check_config.py", json.dumps(payload)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None
        assert output_json["task_needed"] is True
        assert "image" in output_json["changes"]
        assert "schedule" not in output_json["changes"]

        # Verify existing schedule is preserved
        with open(target_config) as f:
            updated_config = json.load(f)
        assert (
            updated_config["dag_configs"][0]["dag_config"]["schedule"] == "30 0 * * *"
        )


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_create_fails_without_schedule(
    mock_temp_dir, mock_get_token, mock_run, tmp_path
):
    mock_get_token.return_value = "dummy-token"

    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = str(tmp_path)
    mock_temp_dir.return_value = mock_temp_dir_instance

    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run.return_value = mock_run_res

    # Missing/empty schedule on create should fail validation (config_only source,
    # which is still legitimately allowed to create a brand-new DAG).
    payload = {
        "source": "config_only",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data",
    }

    with patch("sys.argv", ["check_config.py", json.dumps(payload)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None
        assert output_json["validation_passed"] is False
        assert "new DAG requires a schedule" in output_json["validation_errors"]


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_model_source_refuses_create_for_missing_dag(
    mock_temp_dir, mock_get_token, mock_run, tmp_path
):
    mock_get_token.return_value = "dummy-token"

    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = str(tmp_path)
    mock_temp_dir.return_value = mock_temp_dir_instance

    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run.return_value = mock_run_res

    # A model image-ready event (has schedule, image, tag — everything a "create"
    # would normally need) targeting a dag_id whose config.json does NOT exist in
    # the freshly cloned repo. This must be refused (needs_human), never create.
    payload = {
        "source": "model",
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl",
        "tag": "new-tag",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt_never_onboarded",
        "schedule": "30 0 * * *",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data",
    }

    with patch("sys.argv", ["check_config.py", json.dumps(payload)]):
        printed_lines = []

        def mock_print(*args, **kwargs):
            if args:
                printed_lines.append(args[0])

        with patch("builtins.print", mock_print):
            main()

        output_json = None
        for line in printed_lines:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "resolved_path" in data:
                    output_json = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert output_json is not None
        assert output_json["validation_passed"] is False
        assert output_json["task_needed"] is True
        assert any(
            "refusing to create from scratch" in err
            for err in output_json["validation_errors"]
        )
        # Confirm the create branch's side effects never ran: subprocess.run was
        # only invoked once, for the initial `git clone` — no checkout/commit/push.
        assert output_json["config_content"] == ""
        assert output_json["feature_branch"] == ""
        assert output_json["changes"] == {}
        assert mock_run.call_count == 1
