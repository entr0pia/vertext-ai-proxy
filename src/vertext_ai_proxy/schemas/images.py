from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImageGenerationRequest(BaseModel):
    """OpenAI POST /v1/images/generations 请求体结构。"""

    model_config = ConfigDict(extra="allow")

    model: str
    prompt: str
    n: int = Field(default=1, ge=1, le=8)
    size: str | None = None
    response_format: str | None = "b64_json"
    quality: str | None = None
    style: str | None = None
    user: str | None = None

    @field_validator("model")
    @classmethod
    def validate_model_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("model must not be empty")
        return value.strip()

    @field_validator("prompt")
    @classmethod
    def validate_prompt_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("prompt must not be empty")
        return value.strip()


class ImageDataObject(BaseModel):
    b64_json: str | None = None
    url: str | None = None
    revised_prompt: str | None = None


class ImageResponse(BaseModel):
    created: int
    data: list[ImageDataObject]
