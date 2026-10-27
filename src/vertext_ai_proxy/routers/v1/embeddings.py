from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from vertext_ai_proxy.routers.v1.models import verify_auth
from vertext_ai_proxy.schemas.embeddings import EmbeddingRequest

router = APIRouter(prefix="/v1", tags=["embeddings"])


@router.post("/embeddings")
async def embeddings(
    request: Request,
    req: EmbeddingRequest,
    _: str = Depends(verify_auth),
) -> Response:
    embedding_service = request.app.state.embedding_service
    result = await embedding_service.create_embeddings(req)
    return JSONResponse(status_code=200, content=result.model_dump())
