import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_external_api_no_token(async_client: AsyncClient):
    response = await async_client.get("/api/v1/external/groups")
    assert response.status_code == 401
