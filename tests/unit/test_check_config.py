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
            output_json = json.loads(printed_lines[0])
            assert output_json["resolved_path"] == "dv-stage-eu/sports/dv-sports-elt/config.json"
            assert output_json["exists"] == "yes"
            assert output_json["task"] == "update"
