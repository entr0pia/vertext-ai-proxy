from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SpeechRequest(BaseModel):
    """OpenAI POST /v1/audio/speech 请求体结构。"""

    model_config = ConfigDict(extra="allow")

    model: str
    input: str
    voice: str = "Kore"
    response_format: str | None = None
    speed: float | None = Field(default=None, ge=0.25, le=4.0)
    instructions: str | None = None

    @field_validator("model")
    @classmethod
    def validate_model_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("model must not be empty")
        return value.strip()

    @field_validator("input")
    @classmethod
    def validate_input_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("input must not be empty")
        return value

    @field_validator("voice")
    @classmethod
    def validate_voice_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("voice must not be empty")
        return value.strip()


class TranscriptionResponse(BaseModel):
    text: str


class VerboseTranscriptionResponse(BaseModel):
    task: Literal["transcribe", "translate"] = "transcribe"
    language: str = "unknown"
    duration: float = 0.0
    text: str
    segments: list[dict[str, Any]] = Field(default_factory=list)
