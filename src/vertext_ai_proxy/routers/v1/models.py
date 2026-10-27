from fastapi import APIRouter, Depends, Header, Request, Response
from vertext_ai_proxy.core.exceptions import raise_unauthorized

router = APIRouter(prefix="/v1", tags=["models"])


async def verify_auth(
    request: Request,
    authorization: str | None = Header(None),
    api_key_header: str | None = Header(None, alias="api-key"),
) -> str:
    validator = getattr(request.app.state, "api_key_validator", None)
    if not validator:
        raise_unauthorized("Authentication not initialized.")
    return validator(authorization=authorization, api_key_header=api_key_header)


@router.get("/models")
async def list_models(
    request: Request,
    _: str = Depends(verify_auth),
) -> Response:
    model_service = request.app.state.model_service
    return await model_service.get_models()
