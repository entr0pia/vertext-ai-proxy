from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResponseCreateRequest(BaseModel):
    """OpenAI POST /v1/responses 请求体结构。"""

    model_config = ConfigDict(extra="allow")

    model: str
    input: str | list[dict[str, Any]]
    instructions: str | None = None
    previous_response_id: str | None = None
    store: bool = True
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None
    reasoning: dict[str, Any] | None = None
    text: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("model")
    @classmethod
    def validate_model_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("model must not be empty")
        return value.strip()

    @field_validator("input")
    @classmethod
    def validate_input_non_empty(
        cls, value: str | list[dict[str, Any]]
    ) -> str | list[dict[str, Any]]:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("input must not be empty")
        elif isinstance(value, list):
            if len(value) == 0:
                raise ValueError("input list must not be empty")
        return value


class ResponseDeleteResult(BaseModel):
    """OpenAI DELETE /v1/responses/{response_id} 返回结构。"""

    id: str
    object: Literal["response.deleted"] = "response.deleted"
    deleted: bool = True
