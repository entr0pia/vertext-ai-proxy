import mimetypes
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from vertext_ai_proxy.core.exceptions import OpenAIAPIException, raise_unauthorized
from vertext_ai_proxy.schemas.images import ImageGenerationRequest

router = APIRouter(prefix="/v1", tags=["images"])


async def verify_auth(
    request: Request,
    authorization: str | None = Header(None),
    api_key_header: str | None = Header(None, alias="api-key"),
) -> str:
    validator = getattr(request.app.state, "api_key_validator", None)
    if not validator:
        raise_unauthorized("Authentication not initialized.")
    return validator(authorization=authorization, api_key_header=api_key_header)


def _infer_image_mime(upload: UploadFile) -> str:
    ct = (upload.content_type or "").strip().lower()
    if ct.startswith("image/"):
        return ct
    if upload.filename:
        guessed, _ = mimetypes.guess_type(upload.filename)
        if guessed and guessed.startswith("image/"):
            return guessed
    return "image/png"


@router.post("/images/generations")
async def create_image_generation(
    request: Request,
    body: ImageGenerationRequest,
    _: str = Depends(verify_auth),
) -> JSONResponse:
    image_service = request.app.state.image_service
    result = await image_service.generate_images(body)
    return JSONResponse(status_code=200, content=result.model_dump(exclude_none=True))


@router.post("/images/edits")
async def create_image_edit(
    request: Request,
    _: str = Depends(verify_auth),
) -> JSONResponse:
    form = await request.form()

    model = str(form.get("model") or "").strip()
    prompt = str(form.get("prompt") or "").strip()
    size = str(form.get("size")).strip() if form.get("size") is not None else None
    response_format = (
        str(form.get("response_format")).strip()
        if form.get("response_format") is not None
        else "b64_json"
    )

    n_raw = form.get("n")
    try:
        n = int(str(n_raw)) if n_raw is not None and str(n_raw).strip() else 1
    except ValueError:
        raise OpenAIAPIException(
            status_code=400,
            message="Parameter 'n' must be an integer.",
            code="invalid_parameter",
            param="n",
        )
    if n < 1 or n > 8:
        raise OpenAIAPIException(
            status_code=400,
            message="Parameter 'n' must be between 1 and 8.",
            code="invalid_parameter",
            param="n",
        )

    # 兼容 OpenAI SDK 单图字段 'image' 与多图字段 'image[]'
    raw_image_entries = list(form.getlist("image")) + list(form.getlist("image[]"))
    images: list[tuple[bytes, str]] = []
    for entry in raw_image_entries:
        if isinstance(entry, UploadFile):
            data = await entry.read()
            images.append((data, _infer_image_mime(entry)))

    mask_tuple: tuple[bytes, str] | None = None
    mask_entry = form.get("mask")
    if isinstance(mask_entry, UploadFile):
        mask_data = await mask_entry.read()
        if mask_data:
            mask_tuple = (mask_data, _infer_image_mime(mask_entry))

    image_service = request.app.state.image_service
    result = await image_service.edit_images(
        model=model,
        prompt=prompt,
        images=images,
        mask=mask_tuple,
        n=n,
        size=size,
        response_format=response_format,
    )
    return JSONResponse(status_code=200, content=result.model_dump(exclude_none=True))
