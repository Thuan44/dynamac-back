import logging
from datetime import datetime
from typing import Literal, List
from fastapi import APIRouter, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field, field_validator
import httpx
from services.database import database_service
from services.storage import storage_service
from gemini_pipeline import generate_variant
import uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["generate"])


class VariantRequest(BaseModel):
    """Model for a single variant request."""
    
    hour: str = Field(..., description="Hour in HH:MM format for lighting adjustment")
    format: Literal["thumbnail", "16:9"] = Field(
        default="thumbnail",
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


class GenerateRequest(BaseModel):
    """Request model for image generation endpoint."""
    
    task_id: int = Field(..., description="ID of the task containing the source image")
    variants: List[VariantRequest] = Field(..., description="List of variants to generate")


async def process_generation_background(
    task_id: int,
    variants: List[VariantRequest],
    source_image_url: str
) -> None:
    """
    Background task to process image generation.
    
    Args:
        task_id: ID of the task
        variants: List of variants to generate
        source_image_url: URL of the source image
    """
    try:
        # Download source image from URL
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(source_image_url)
                response.raise_for_status()
                image_bytes = response.content
            except httpx.HTTPError as e:
                logger.error(f"Failed to download image from {source_image_url}: {str(e)}")
                # Reset state on error
                try:
                    database_service.update_task_state(task_id, "configured")
                except:
                    pass
                return
        
        # Generate all variants
        preview_urls = []
        errors = []
        
        for variant in variants:
            try:
                # Generate variant using Gemini
                generated_image_bytes, auto_uploaded_url = generate_variant(
                    source=image_bytes,
                    hour=variant.hour,
                    format_type=variant.format
                )
                
                # Use auto-uploaded URL if available (for thumbnails), otherwise upload manually
                if auto_uploaded_url:
                    preview_url = auto_uploaded_url
                else:
                    # Generate unique file name for preview
                    # Format: preview_{hour_without_colon}.png (e.g., preview_0800.png)
                    hour_filename = variant.hour.replace(":", "")
                    preview_name = f"preview_{hour_filename}_{uuid.uuid4().hex[:8]}.png"
                    
                    # Upload to 'previews' bucket
                    preview_url = storage_service.upload_to_bucket(
                        file_content=generated_image_bytes,
                        file_name=preview_name,
                        content_type="image/png",
                        bucket="previews"
                    )
                
                preview_urls.append({
                    "hour": variant.hour,
                    "url": preview_url
                })
                
                logger.info(f"Successfully generated and uploaded variant for hour {variant.hour}")
                
            except ValueError as e:
                error_msg = f"Validation error for variant {variant.hour}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
            except Exception as e:
                error_msg = f"Failed to generate variant for hour {variant.hour}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # If all variants failed, update state back to configured
        if len(preview_urls) == 0:
            try:
                database_service.update_task_state(task_id, "configured")
            except:
                pass
            logger.error(f"All variants failed for task {task_id}. Errors: {errors}")
            return
        
        # Update task with preview URLs and set state to "previews_ready"
        try:
            database_service.update_task_previews(task_id, preview_urls, "previews_ready")
            logger.info(f"Task {task_id} updated with {len(preview_urls)} preview URLs")
        except Exception as e:
            logger.error(f"Failed to update task with preview URLs: {str(e)}")
            # Try to reset state
            try:
                database_service.update_task_state(task_id, "configured")
            except:
                pass
            
    except Exception as e:
        logger.error(f"Unexpected error in background generation for task {task_id}: {str(e)}")
        # Try to reset state on unexpected error
        try:
            database_service.update_task_state(task_id, "configured")
        except:
            pass


@router.post("/generate")
async def generate_image(request: GenerateRequest, background_tasks: BackgroundTasks):
    """
    Generate multiple variants of an uploaded image using Gemini AI.
    
    This endpoint:
    - Validates the request and task
    - Sets task state to "processing"
    - Returns immediately with status "processing"
    - Processes generation in background
    - Updates task with preview URLs and sets state to "previews_ready" when done
    
    Args:
        request: GenerateRequest with task_id and list of variants
        background_tasks: FastAPI BackgroundTasks instance
        
    Returns:
        JSON response with task_id and status "processing"
        
    Raises:
        HTTPException: If validation fails or task not found
    """
    try:
        # Validate that variants list is not empty
        if not request.variants or len(request.variants) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one variant must be provided"
            )
        
        # Get task from database to validate it exists
        try:
            task = database_service.get_task(request.task_id)
        except Exception as e:
            logger.error(f"Task not found: {request.task_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task not found: {str(e)}"
            )
        
        # Update state to "processing"
        try:
            database_service.update_task_state(request.task_id, "processing")
        except Exception as e:
            logger.error(f"Failed to update task state to processing: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update task state: {str(e)}"
            )
        
        # Add background task to process generation
        background_tasks.add_task(
            process_generation_background,
            task_id=request.task_id,
            variants=request.variants,
            source_image_url=task.url_source
        )
        
        # Return immediately
        return {
            "task_id": str(request.task_id),
            "status": "processing"
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error in generate endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


async def process_upscale_background(task_id: int) -> None:
    """
    Background task to upscale previews to full-size renders.
    
    Steps:
    - Fetch previews from urls_previews.variants
    - Download the original source image once
    - Regenerate with format_type="16:9" while keeping the hour metadata
    - Upload to 'upscaled' bucket and store urls_renders
    """
    try:
        task_data = database_service.get_task_raw(task_id)
        urls_previews = task_data.get("urls_previews", {})
        variants = urls_previews.get("variants", [])
        source_url = task_data.get("url_source")
        
        if not variants:
            logger.error(f"No previews found to upscale for task {task_id}")
            database_service.update_task_state(task_id, "previews_ready")
            return
        if not source_url:
            logger.error(f"No source URL found to upscale for task {task_id}")
            database_service.update_task_state(task_id, "previews_ready")
            return
        
        renders = []
        errors = []
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                source_response = await client.get(source_url)
                source_response.raise_for_status()
                source_bytes = source_response.content
            except httpx.HTTPError as e:
                logger.error(f"Failed to download source image {source_url}: {str(e)}")
                database_service.update_task_state(task_id, "previews_ready")
                return
            
            for variant in variants:
                hour = variant.get("hour")
                
                if not hour:
                    errors.append(f"Missing hour for variant: {variant}")
                    continue
                
                try:
                    render_bytes, _ = generate_variant(
                        source=source_bytes,
                        hour=hour,
                        format_type="16:9"
                    )
                    
                    render_name = f"render_{hour.replace(':', '')}_{uuid.uuid4().hex[:8]}.png"
                    render_url = storage_service.upload_to_bucket(
                        file_content=render_bytes,
                        file_name=render_name,
                        content_type="image/png",
                        bucket="upscaled"
                    )
                    
                    renders.append({
                        "hour": hour,
                        "url": render_url
                    })
                    
                    logger.info(f"Successfully upscaled render for hour {hour}")
                except Exception as e:
                    error_msg = f"Failed to upscale variant {hour}: {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)
        
        if len(renders) == 0:
            logger.error(f"All upscales failed for task {task_id}. Errors: {errors}")
            database_service.update_task_state(task_id, "previews_ready")
            return
        
        try:
            database_service.update_task_renders(task_id, renders, "renders_ready")
            logger.info(f"Task {task_id} updated with {len(renders)} renders")
        except Exception as e:
            logger.error(f"Failed to update task with renders: {str(e)}")
            try:
                database_service.update_task_state(task_id, "previews_ready")
            except:
                pass
    except Exception as e:
        logger.error(f"Unexpected error in background upscale for task {task_id}: {str(e)}")
        try:
            database_service.update_task_state(task_id, "previews_ready")
        except:
            pass


@router.post("/task/{task_id}/upscale")
async def upscale_task(task_id: int, background_tasks: BackgroundTasks):
    """
    Upscale generated previews to full-size renders (16:9) while keeping hours.
    
    Preconditions:
    - Task must exist
    - Task state must be "previews_ready"
    - urls_previews.variants must contain hour/url entries
    """
    try:
        task = database_service.get_task_raw(task_id)
        if task.get("state") != "previews_ready":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task is not ready for upscale. State must be 'previews_ready'."
            )
        
        urls_previews = task.get("urls_previews", {})
        variants = urls_previews.get("variants", [])
        if not variants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No previews available to upscale."
            )
        
        try:
            database_service.update_task_state(task_id, "upscaling")
        except Exception as e:
            logger.error(f"Failed to update task state to upscaling: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update task state: {str(e)}"
            )
        
        background_tasks.add_task(process_upscale_background, task_id=task_id)
        
        return {
            "task_id": str(task_id),
            "status": "upscaling"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in upscale endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

