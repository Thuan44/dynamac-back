import logging
from fastapi import APIRouter, HTTPException, status, BackgroundTasks

from schemas.generate import GenerateRequest
from services.database import database_service
from services.generation import process_generation_background
from services.upscale import process_upscale_background

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["generate"])


@router.post("/generate")
async def generate_image(request: GenerateRequest, background_tasks: BackgroundTasks):
    """
    Generate multiple variants of an uploaded image using Gemini AI.

    - Validate request and task
    - Set task state to "processing"
    - Spawn background generation task
    - Return immediately with processing status
    """
    try:
        if not request.variants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one variant must be provided",
            )

        try:
            task = database_service.get_task(request.task_id)
        except Exception as e:
            logger.error(f"Task not found: {request.task_id}, error: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found",
            )

        try:
            database_service.update_task_state(request.task_id, "processing")
        except Exception as e:
            logger.error(f"Failed to update task state to processing: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update task state",
            )

        background_tasks.add_task(
            process_generation_background,
            task_id=request.task_id,
            variants=request.variants,
            source_image_url=task.url_source,
        )

        return {"task_id": str(request.task_id), "status": "processing"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in generate endpoint: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.post("/task/{task_id}/upscale")
async def upscale_task(task_id: int, background_tasks: BackgroundTasks):
    """
    Upscale generated previews to full-size renders (16:9) while keeping hours.
    """
    try:
        task = database_service.get_task_raw(task_id)
        if task.get("state") != "previews_ready":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task is not ready for upscale. State must be 'previews_ready'.",
            )

        urls_previews = task.get("urls_previews", {})
        variants = urls_previews.get("variants", [])
        if not variants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No previews available to upscale.",
            )

        try:
            database_service.update_task_state(task_id, "upscaling")
        except Exception as e:
            logger.error(f"Failed to update task state to upscaling: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update task state",
            )

        background_tasks.add_task(process_upscale_background, task_id=task_id)

        return {"task_id": str(task_id), "status": "upscaling"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in upscale endpoint: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )

