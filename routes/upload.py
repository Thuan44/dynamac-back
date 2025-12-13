import logging
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException, status, Request
from utils.rate_limiter import limiter
from services.storage import storage_service
from services.database import database_service
from models.task import TaskCreate, UploadResponse
from utils.file_validation import validate_uploaded_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/10 minutes")
async def upload_image(request: Request, file: UploadFile = File(...)):
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
        # Read file content
        file_content = await file.read()
        
        # Validate file (size, magic bytes, image integrity)
        # This validates the actual file content, not just the client-provided content-type
        # SECURITY: Client-provided filename extension is completely ignored and never used
        mime_type, file_extension = validate_uploaded_file(
            file_content=file_content,
            client_content_type=file.content_type,
            client_filename=file.filename  # Only for logging/warning, never trusted
        )
        
        # Generate unique file name using validated extension
        # IMPORTANT: We completely ignore the client-provided filename extension
        # The extension is determined solely from the validated MIME type (magic bytes)
        file_name = f"{uuid.uuid4()}.{file_extension}"
        
        # Upload to Supabase Storage using validated MIME type
        public_url = storage_service.upload_file(
            file_content=file_content,
            file_name=file_name,
            content_type=mime_type
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
        # Log detailed error for debugging (server-side only)
        logger.error(f"Upload failed: {str(e)}", exc_info=True)
        
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
        
        # Generalize error message for security (don't expose internal details)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file"
        )

