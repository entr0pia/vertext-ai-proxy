import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest
from google.oauth2 import service_account
from vertext_ai_proxy.auth import GCPTokenManager
from vertext_ai_proxy.config import GCPConfig


def create_mock_credentials(
    project_id: str = "mock-proj-123",
    token: str = "initial-mock-token",
    expiry_seconds: int = 3600,
) -> MagicMock:
    creds = MagicMock(spec=service_account.Credentials)
    creds.project_id = project_id
    creds.token = token
    creds.expiry = datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds)
    creds.valid = True

    def fake_refresh(request):
        creds.token = "refreshed-mock-token"
        creds.expiry = datetime.now(timezone.utc) + timedelta(seconds=3600)

    creds.refresh.side_effect = fake_refresh
    return creds


def test_gcp_token_manager_initialization():
    creds = create_mock_credentials(project_id="auto-detected-proj")
    config = GCPConfig(location="global")

    manager = GCPTokenManager(config, credentials=creds)
    assert manager.project_id == "auto-detected-proj"
    assert manager.location == "global"
    assert (
        manager.base_upstream_url
        == "https://aiplatform.googleapis.com/v1/projects/auto-detected-proj/locations/global/endpoints/openapi"
    )


def test_gcp_token_manager_explicit_project_override():
    creds = create_mock_credentials(project_id="auto-detected-proj")
    config = GCPConfig(project_id="manual-override-proj", location="us-central1")

    manager = GCPTokenManager(config, credentials=creds)
    assert manager.project_id == "manual-override-proj"
    assert manager.location == "us-central1"


def test_missing_file_raises_error(tmp_path):
    config = GCPConfig(service_account_file=str(tmp_path / "non_existent.json"))
    with pytest.raises(FileNotFoundError):
        GCPTokenManager(config)


@pytest.mark.asyncio
async def test_get_access_token_cached_when_valid():
    creds = create_mock_credentials(token="valid-cached-token", expiry_seconds=1800)
    config = GCPConfig()
    manager = GCPTokenManager(config, credentials=creds)

    token = await manager.get_access_token()
    assert token == "valid-cached-token"
    creds.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_get_access_token_refreshes_when_expiring():
    # Token expiring in 100 seconds (< 300 buffer)
    creds = create_mock_credentials(token="expiring-token", expiry_seconds=100)
    config = GCPConfig()
    manager = GCPTokenManager(config, credentials=creds)

    token = await manager.get_access_token()
    assert token == "refreshed-mock-token"
    creds.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_concurrent_token_refresh():
    creds = create_mock_credentials(token="expired-token", expiry_seconds=-10)
    config = GCPConfig()
    manager = GCPTokenManager(config, credentials=creds)

    # Launch 5 concurrent requests for token
    tokens = await asyncio.gather(*[manager.get_access_token() for _ in range(5)])

    assert all(t == "refreshed-mock-token" for t in tokens)
    # Lock ensures refresh was only called once
    assert creds.refresh.call_count == 1
