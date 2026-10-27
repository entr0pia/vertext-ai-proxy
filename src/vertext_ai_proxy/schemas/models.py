from typing import Any, Literal
from pydantic import BaseModel, Field


class ModelCard(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "google"
    permission: list[Any] = Field(default_factory=list)
    root: str | None = None
    parent: str | None = None


class ModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelCard]
