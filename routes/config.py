import logging
from datetime import datetime
from typing import List, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from services.database import database_service


logger = logging.getLogger(__name__)


class VariantConfig(BaseModel):
    """Configuration for a single wallpaper variant."""

    slot: int = Field(..., ge=0)
    source: Literal["original", "ai"]
    hour: str

    @field_validator("hour")
    @classmethod
    def validate_hour_format(cls, value: str) -> str:
        """Validate that hour is in HH:MM 24-hour format."""
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            raise ValueError("hour must be in HH:MM format")
        return value


class TaskConfigPayload(BaseModel):
    """Payload for configuring a task."""

    variants: List[VariantConfig]

    @field_validator("variants")
    @classmethod
    def validate_variants_not_empty(cls, value: List[VariantConfig]) -> List[VariantConfig]:
        """Ensure that variants list is not empty."""
        if not value:
            raise ValueError("variants must be a non-empty array")
        return value


router = APIRouter(prefix="/api", tags=["config"])


@router.post(
    "/task/{task_id}/config",
    status_code=status.HTTP_200_OK,
)
async def configure_task(task_id: UUID, payload: TaskConfigPayload):
    """
    Configure a task with a list of variants.

    This endpoint:
    - validates the payload (non-empty variants list, correct types and formats)
    - updates the task record in database with config and state='configured'
    - returns a simple status response
    """
    try:
        database_service.update_task_config(
            task_id=task_id,
            config_payload=payload.model_dump(),
        )
        logger.info(f"Task configured successfully: {task_id}")
        return {"status": "ok"}
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as exc:
        logger.error(f"Failed to configure task {task_id}: {str(exc)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to configure task",
        )


