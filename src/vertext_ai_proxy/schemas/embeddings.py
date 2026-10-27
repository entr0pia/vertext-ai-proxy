from typing import Literal
from pydantic import BaseModel, Field, field_validator


class EmbeddingRequest(BaseModel):
    """OpenAI 兼容的嵌入请求结构。"""

    input: str | list[str]
    model: str
    encoding_format: Literal["float", "base64"] = "float"
    dimensions: int | None = Field(default=None, gt=0)
    user: str | None = None

    @field_validator("input")
    @classmethod
    def validate_input(cls, v: str | list[str]) -> str | list[str]:
        if isinstance(v, str):
            if not v.strip():
                raise ValueError("Input string must not be empty.")
            return v
        elif isinstance(v, list):
            if len(v) == 0:
                raise ValueError("Input array must contain at least one string.")
            if len(v) > 250:
                raise ValueError("Input array exceeds maximum batch limit of 250 items.")
            for i, item in enumerate(v):
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(f"Input at index {i} must be a non-empty string.")
            return v
        raise ValueError("Input must be a string or array of strings.")

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Model name must not be empty.")
        return cleaned


class EmbeddingObject(BaseModel):
    """单条嵌入向量结果。"""

    object: Literal["embedding"] = "embedding"
    index: int
    embedding: list[float]


class EmbeddingUsage(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0


class EmbeddingResponse(BaseModel):
    """OpenAI 兼容的嵌入响应结构。"""

    object: Literal["list"] = "list"
    data: list[EmbeddingObject]
    model: str
    usage: EmbeddingUsage
