from datetime import datetime
from typing import Literal, List
from pydantic import BaseModel, Field, field_validator


class VariantRequest(BaseModel):
    """Model for a single variant request."""

    hour: str = Field(..., description="Hour in HH:MM format for lighting adjustment")
    format: Literal["thumbnail", "16:9"] = Field(
        default="thumbnail",
        description="Output format: 'thumbnail' (512x288) or '16:9' (2560x1440)",
    )

    @field_validator("hour")
    @classmethod
    def validate_hour_format(cls, value: str) -> str:
        """Validate that hour is in HH:MM 24-hour format."""
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            raise ValueError("hour must be in HH:MM format")
        return value


class GenerateRequest(BaseModel):
    """Request model for image generation endpoint."""

    task_id: int = Field(..., description="ID of the task containing the source image")
    variants: List[VariantRequest] = Field(..., description="List of variants to generate")
