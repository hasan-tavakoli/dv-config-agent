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
import pytest
from unittest.mock import patch, MagicMock
from scripts.check_config import resolve_path, main

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
def test_main_exists(mock_temp_dir, mock_get_token, mock_run):
    mock_get_token.return_value = "dummy-token"
    
    # Mock TemporaryDirectory context manager
    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = "/tmp/dummy-clone"
    mock_temp_dir.return_value = mock_temp_dir_instance
    
    # Mock Path.exists to return True
    with patch("pathlib.Path.exists", return_value=True):
        payload = {
            "environment": "stage",
            "domain": "sports",
            "dag_id": "dv_sports_elt"
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
            assert output_json is not None, f"Could not find valid JSON in printed lines: {printed_lines}"
            assert output_json["resolved_path"] == "dv-stage-eu/sports/dv-sports-elt/config.json"
            assert output_json["exists"] == "yes"
            assert output_json["task"] == "update"


@patch("subprocess.run")
@patch("scripts.check_config.get_github_token")
@patch("tempfile.TemporaryDirectory")
def test_main_create(mock_temp_dir, mock_get_token, mock_run, tmp_path):
    mock_get_token.return_value = "dummy-token"
    
    # Mock TemporaryDirectory context manager to point to our test tmp_path
    mock_temp_dir_instance = MagicMock()
    mock_temp_dir_instance.__enter__.return_value = str(tmp_path)
    mock_temp_dir.return_value = mock_temp_dir_instance
    
    payload = {
        "image": "ghcr.io/hasan-tavakoli/dv-sports-etl",
        "tag": "feature-add-model-daily_active_customers-1782992764-24fc831",
        "domain": "sports",
        "environment": "stage",
        "dag_id": "dv_sports_elt",
        "schedule": "30 0 * * *",
        "service_account": "analytics-dev@dv-dev-eu-w1-sports-elt.iam.gserviceaccount.com",
        "execution_project": "dv-dev-eu-w1-sports-elt",
        "target_project": "dv-dev-eu-w1-sports-data"
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
        assert output_json is not None, f"Could not find valid JSON in printed lines: {printed_lines}"
        assert output_json["resolved_path"] == "dv-stage-eu/sports/dv-sports-elt/config.json"
        assert output_json["exists"] == "no"
        assert output_json["task"] == "create"
        
        # Verify created files exist and check content
        created_config_path = tmp_path / "dv-stage-eu/sports/dv-sports-elt/config.json"
        created_deploy_path = tmp_path / "dv-stage-eu/sports/dv-sports-elt/deploy/deploy.yml"
        
        assert created_config_path.exists()
        assert created_deploy_path.exists()
        
        # Read and check config.json content
        with open(created_config_path) as f:
            config_data = json.load(f)
            
        assert config_data["dag_configs"][0]["dag_config"]["dag_id"] == "dv_sports_elt"
        assert config_data["dag_configs"][0]["dag_config"]["schedule"] == "30 0 * * *"
        assert config_data["dag_configs"][0]["job_config"]["env_variables"]["DBT_EXECUTION_PROJECT"] == "dv-dev-eu-w1-sports-elt"
        assert config_data["dag_configs"][0]["job_config"]["env_variables"]["DBT_PROJECT"] == "dv-dev-eu-w1-sports-data"
        assert config_data["dag_configs"][0]["job_config"]["env_variables"]["DBT_LOCATION"] == "europe-west1"
        
        # Read and check deploy.yml content
        with open(created_deploy_path) as f:
            deploy_text = f.read()
            
        assert "name: dv-sports-elt" in deploy_text
        assert "image: ghcr.io/hasan-tavakoli/dv-sports-etl:feature-add-model-daily_active_customers-1782992764-24fc831" in deploy_text
        assert "region: europe-west1" in deploy_text

