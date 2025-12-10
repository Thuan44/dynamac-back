import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from services.database import database_service


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api", tags=["status"])


class PreviewUrl(BaseModel):
    """Model for a single preview URL."""
    heure: str
    url: str


class TaskStatusResponse(BaseModel):
    """Response model for task status endpoint."""
    taskId: str
    status: str
    urls_previews: Optional[List[PreviewUrl]] = None
    urls_renders: Optional[List[PreviewUrl]] = None
    url_finale: Optional[str] = None


@router.get(
    "/task/{task_id}/status",
    status_code=status.HTTP_200_OK,
    response_model=TaskStatusResponse,
)
async def get_task_status(task_id: int):
    """
    Get the status of a task and its preview URLs if available.
    
    Returns:
        - If status is "processing": {"taskId": "123", "status": "processing", "urls_previews": null}
        - If status is "previews_ready": {"taskId": "123", "status": "previews_ready", "urls_previews": [...]}
        - If status is "renders_ready": {"taskId": "123", "status": "renders_ready", "urls_renders": [...]}
        - For other statuses: {"taskId": "123", "status": "...", "urls_previews": null, "urls_renders": null}
    """
    try:
        # Get raw task data to access urls_previews
        task_data = database_service.get_task_raw(task_id)
        
        task_state = task_data.get("state", "unknown")
        urls_previews_data = task_data.get("urls_previews")
        urls_renders_data = task_data.get("urls_renders")
        url_finale = task_data.get("url_finale")
        
        # Extract previews if available and status is previews_ready
        previews = None
        if task_state in {"previews_ready", "renders_ready"} and urls_previews_data:
            # urls_previews is stored as {"variants": [...]}
            variants = urls_previews_data.get("variants", [])
            if variants:
                # Convert "hour" to "heure" as per API specification
                previews = [
                    PreviewUrl(heure=variant.get("hour", ""), url=variant.get("url", ""))
                    for variant in variants
                ]
        
        # Extract renders if available and status is renders_ready
        renders = None
        if task_state == "renders_ready" and urls_renders_data:
            variants = urls_renders_data.get("variants", [])
            if variants:
                renders = [
                    PreviewUrl(heure=variant.get("hour", ""), url=variant.get("url", ""))
                    for variant in variants
                ]
        
        return TaskStatusResponse(
            taskId=str(task_id),
            status=task_state,
            urls_previews=previews,
            urls_renders=renders,
            url_finale=url_finale
        )
        
    except Exception as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            logger.error(f"Task not found: {task_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task not found: {task_id}"
            )
        else:
            logger.error(f"Failed to get task status {task_id}: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get task status: {error_msg}"
            )

