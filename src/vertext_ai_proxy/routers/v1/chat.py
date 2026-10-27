from typing import Any
from fastapi import APIRouter, Depends, Request, Response
from vertext_ai_proxy.routers.v1.models import verify_auth

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    _: str = Depends(verify_auth),
) -> Response:
    payload: dict[str, Any] = await request.json()
    chat_service = request.app.state.chat_service
    return await chat_service.forward_chat_completions(request, payload)
