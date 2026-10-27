from typing import Any
from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def health_check(request: Request) -> dict[str, Any]:
    proxy = getattr(request.app.state, "proxy", None)
    project_id = proxy.client.project_id if proxy else ""
    location = proxy.client.location if proxy else ""
    return {
        "status": "healthy",
        "project_id": project_id,
        "location": location,
    }
