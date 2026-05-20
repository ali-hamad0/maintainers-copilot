"""Pydantic domain models for WidgetConfig.

Distinct from the ORM model in repositories/orm_models.py.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class WidgetConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    allowed_origins: list[str] = Field(
        default_factory=list,
        description="Exact origins allowed to embed this widget, e.g. 'http://localhost:8080'.",
    )


class WidgetConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    allowed_origins: list[str] | None = None
    is_active: bool | None = None


class WidgetConfigRead(BaseModel):
    id: uuid.UUID
    name: str
    allowed_origins: list[str]
    owner_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
