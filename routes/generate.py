import logging
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, HTTPException, status, Response
from pydantic import BaseModel, Field, field_validator
import httpx
from services.database import database_service
from gemini_pipeline import generate_variant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["generate"])


class GenerateRequest(BaseModel):
    """Request model for image generation endpoint."""
    
    task_id: int = Field(..., description="ID of the task containing the source image")
    hour: str = Field(..., description="Hour in HH:MM format for lighting adjustment")
    format: Literal["thumbnail", "16:9"] = Field(
        default="16:9",
        description="Output format: 'thumbnail' (512x288) or '16:9' (1920x1080)"
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


@router.post("/generate")
async def generate_image(request: GenerateRequest):
    """
    Generate a variant of an uploaded image using Gemini AI.
    
    This endpoint:
    - Retrieves the task and source image URL
    - Downloads the source image
    - Generates a variant with lighting adjusted for the specified hour
    - Returns the generated image in the requested format
    
    Args:
        request: GenerateRequest with task_id, hour, and format
        
    Returns:
        PNG image bytes in the requested format
        
    Raises:
        HTTPException: If task not found, image download fails, or generation fails
    """
    try:
        # Get task from database
        task = database_service.get_task(request.task_id)
        
        # Download source image from URL
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(task.url_source)
                response.raise_for_status()
                image_bytes = response.content
            except httpx.HTTPError as e:
                logger.error(f"Failed to download image from {task.url_source}: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to download source image: {str(e)}"
                )
        
        # Generate variant using Gemini
        try:
            generated_image_bytes = generate_variant(
                source=image_bytes,
                hour=request.hour,
                format_type=request.format
            )
        except ValueError as e:
            logger.error(f"Validation error in generate_variant: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except Exception as e:
            logger.error(f"Failed to generate variant: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate image variant: {str(e)}"
            )
        
        # Return image as streaming response
        return Response(
            content=generated_image_bytes,
            media_type="image/png",
            headers={
                "Content-Disposition": f'inline; filename="generated_{request.format}_{request.hour.replace(":", "-")}.png"'
            }
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error in generate endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

