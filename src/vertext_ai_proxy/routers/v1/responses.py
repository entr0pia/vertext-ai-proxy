from fastapi import APIRouter, Depends, Header, Request, Response

from vertext_ai_proxy.core.exceptions import raise_unauthorized
from vertext_ai_proxy.schemas.responses import ResponseCreateRequest

router = APIRouter(prefix="/v1", tags=["responses"])


async def verify_auth(
    request: Request,
    authorization: str | None = Header(None),
    api_key_header: str | None = Header(None, alias="api-key"),
) -> str:
    validator = getattr(request.app.state, "api_key_validator", None)
    if not validator:
        raise_unauthorized("Authentication not initialized.")
    return validator(authorization=authorization, api_key_header=api_key_header)


@router.post("/responses")
async def create_response(
    request: Request,
    body: ResponseCreateRequest,
    _: str = Depends(verify_auth),
) -> Response:
    response_service = request.app.state.response_service
    return await response_service.create_response(body)


@router.get("/responses/{response_id}")
async def retrieve_response(
    response_id: str,
    request: Request,
    _: str = Depends(verify_auth),
) -> Response:
    response_service = request.app.state.response_service
    return await response_service.get_response(response_id)


@router.delete("/responses/{response_id}")
async def delete_response(
    response_id: str,
    request: Request,
    _: str = Depends(verify_auth),
) -> Response:
    response_service = request.app.state.response_service
    return await response_service.delete_response(response_id)


@router.post("/responses/{response_id}/cancel")
async def cancel_response(
    response_id: str,
    request: Request,
    _: str = Depends(verify_auth),
) -> Response:
    response_service = request.app.state.response_service
    return await response_service.cancel_response(response_id)
