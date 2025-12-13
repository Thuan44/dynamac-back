"""
File validation utilities for secure file uploads.

This module provides validation functions to ensure uploaded files are:
- Valid image files (verified by magic bytes)
- Safe to process (verified by PIL)
- Within allowed types and sizes
"""

import logging
from io import BytesIO
from typing import Tuple, Optional
import filetype
from PIL import Image
from fastapi import HTTPException, status
from config import settings

logger = logging.getLogger(__name__)


def validate_file_content(file_content: bytes) -> Tuple[str, str]:
    """
    Validate file content by checking magic bytes and image integrity.
    
    Args:
        file_content: Raw file content as bytes
        
    Returns:
        Tuple of (mime_type, file_extension) if valid
        
    Raises:
        HTTPException: If file is invalid or not an allowed image type
    """
    # Check magic bytes to determine real file type
    kind = filetype.guess(file_content)
    
    if not kind:
        logger.warning("File type could not be determined from magic bytes")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Could not determine file type from content."
        )
    
    mime_type = kind.mime
    
    # Verify it's an allowed image type
    if mime_type not in settings.allowed_image_types:
        logger.warning(f"File type {mime_type} is not in allowed types")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type: {mime_type}. Allowed types: {', '.join(settings.allowed_image_types)}"
        )
    
    # Verify it's a valid image using PIL
    try:
        image = Image.open(BytesIO(file_content))
        # Verify image integrity (checks for corrupted files)
        image.verify()
        
        # Reopen image after verify() (verify() closes the image)
        image = Image.open(BytesIO(file_content))
        
        # Additional check: ensure it's actually an image format we support
        if image.format not in ['JPEG', 'PNG', 'WEBP']:
            logger.warning(f"Image format {image.format} is not supported")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported image format: {image.format}"
            )
        
        image.close()
        
    except Exception as e:
        logger.error(f"Image validation failed: {str(e)}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or corrupted image file"
        )
    
    # Map MIME type to file extension
    extension_map = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp"
    }
    
    file_extension = extension_map.get(mime_type, "jpg")
    
    return mime_type, file_extension


def validate_file_size(file_content: bytes) -> None:
    """
    Validate that file size is within allowed limits.
    
    Args:
        file_content: Raw file content as bytes
        
    Raises:
        HTTPException: If file size exceeds maximum allowed size
    """
    file_size = len(file_content)
    
    if file_size > settings.max_file_size:
        max_size_mb = settings.max_file_size / (1024 * 1024)
        logger.warning(f"File size {file_size} exceeds maximum {settings.max_file_size}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds maximum allowed size of {max_size_mb}MB"
        )
    
    # Also check minimum size (very small files are likely invalid)
    if file_size < 100:  # 100 bytes minimum
        logger.warning(f"File size {file_size} is too small")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size is too small to be a valid image"
        )


def validate_uploaded_file(file_content: bytes, client_content_type: Optional[str] = None) -> Tuple[str, str]:
    """
    Complete validation of an uploaded file.
    
    This function performs all security checks:
    1. Validates file size
    2. Validates magic bytes (real file type)
    3. Validates image integrity with PIL
    4. Verifies file type matches allowed types
    
    Args:
        file_content: Raw file content as bytes
        client_content_type: Content type provided by client (for logging only, not trusted)
        
    Returns:
        Tuple of (mime_type, file_extension) if valid
        
    Raises:
        HTTPException: If any validation fails
    """
    # Log client-provided content type for debugging (but don't trust it)
    if client_content_type:
        logger.debug(f"Client provided content-type: {client_content_type}")
    
    # Validate file size first (fast check)
    validate_file_size(file_content)
    
    # Validate file content (magic bytes + PIL)
    mime_type, file_extension = validate_file_content(file_content)
    
    # Warn if client content type doesn't match real type
    if client_content_type and client_content_type != mime_type:
        logger.warning(
            f"Content type mismatch: client provided '{client_content_type}', "
            f"but file is actually '{mime_type}'"
        )
    
    logger.info(f"File validation successful: type={mime_type}, extension={file_extension}")
    
    return mime_type, file_extension
