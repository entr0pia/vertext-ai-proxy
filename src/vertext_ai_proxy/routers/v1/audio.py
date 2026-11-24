from fastapi import APIRouter, Depends, File, Form, Header, Request, Response, UploadFile

from vertext_ai_proxy.core.exceptions import raise_unauthorized
from vertext_ai_proxy.schemas.audio import SpeechRequest

router = APIRouter(prefix="/v1/audio", tags=["audio"])


async def verify_auth(
    request: Request,
    authorization: str | None = Header(None),
    api_key_header: str | None = Header(None, alias="api-key"),
) -> str:
    validator = getattr(request.app.state, "api_key_validator", None)
    if not validator:
        raise_unauthorized("Authentication not initialized.")
    return validator(authorization=authorization, api_key_header=api_key_header)


@router.post("/speech")
async def create_speech(
    request: Request,
    body: SpeechRequest,
    _: str = Depends(verify_auth),
) -> Response:
    audio_service = request.app.state.audio_service
    return await audio_service.create_speech(body)


@router.post("/transcriptions")
async def create_transcription(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
    response_format: str = Form("json"),
    temperature: float | None = Form(None),
    _: str = Depends(verify_auth),
) -> Response:
    audio_bytes = await file.read()
    audio_service = request.app.state.audio_service
    return await audio_service.create_transcription(
        model=model,
        audio_bytes=audio_bytes,
        filename=file.filename,
        content_type=file.content_type,
        language=language,
        prompt=prompt,
        response_format=response_format,
        temperature=temperature,
    )


@router.post("/translations")
async def create_translation(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(...),
    prompt: str | None = Form(None),
    response_format: str = Form("json"),
    temperature: float | None = Form(None),
    _: str = Depends(verify_auth),
) -> Response:
    audio_bytes = await file.read()
    audio_service = request.app.state.audio_service
    return await audio_service.create_translation(
        model=model,
        audio_bytes=audio_bytes,
        filename=file.filename,
        content_type=file.content_type,
        prompt=prompt,
        response_format=response_format,
        temperature=temperature,
    )
