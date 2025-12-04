import logging
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException, status
from services.storage import storage_service
from services.database import database_service
from models.task import TaskCreate, UploadResponse
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(file: UploadFile = File(...)):
    """
    Upload an image file to Supabase Storage and create a task record.
    
    Args:
        file: Image file uploaded via multipart/form-data
        
    Returns:
        UploadResponse with task_id and url_source
        
    Raises:
        HTTPException: If validation or upload fails
    """
    file_name = None
    task_id = None
    
    try:
        # Validate file type
        if file.content_type not in settings.allowed_image_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file type. Allowed types: {', '.join(settings.allowed_image_types)}"
            )
        
        # Read file content
        file_content = await file.read()
        
        # Validate file size
        if len(file_content) > settings.max_file_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File size exceeds maximum allowed size of {settings.max_file_size / (1024 * 1024)}MB"
            )
        
        # Generate unique file name
        file_extension = file.filename.split(".")[-1] if "." in file.filename else "jpg"
        file_name = f"{uuid.uuid4()}.{file_extension}"
        
        # Upload to Supabase Storage
        public_url = storage_service.upload_file(
            file_content=file_content,
            file_name=file_name,
            content_type=file.content_type
        )
        
        # Create task in database
        task_data = TaskCreate(url_source=public_url)
        task = database_service.create_task(task_data)
        task_id = task.id
        
        logger.info(f"Upload successful: task_id={task_id}, file={file_name}")
        
        return UploadResponse(
            task_id=task.id,
            url_source=public_url,
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        
        # Rollback: delete file if it was uploaded
        if file_name:
            try:
                storage_service.delete_file(file_name)
            except Exception as rollback_error:
                logger.error(f"Rollback failed (file deletion): {str(rollback_error)}")
        
        # Rollback: delete task if it was created
        if task_id:
            try:
                database_service.delete_task(task_id)
            except Exception as rollback_error:
                logger.error(f"Rollback failed (task deletion): {str(rollback_error)}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {str(e)}"
        )

