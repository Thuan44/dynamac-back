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


def _extract_extension_from_filename(filename: Optional[str]) -> Optional[str]:
    """
    Extract file extension from filename (for validation purposes only).
    
    This is used to detect mismatches between client-provided filename extension
    and the actual file type, but the client extension is NEVER trusted or used.
    
    Args:
        filename: Filename provided by client (may be None)
        
    Returns:
        Extension without the dot (e.g., "jpg", "png") or None if not found
    """
    if not filename:
        return None
    
    # Find last dot in filename
    if "." not in filename:
        return None
    
    # Get extension after last dot
    extension = filename.rsplit(".", 1)[-1].lower()
    return extension if extension else None


def _normalize_extension(ext: str) -> str:
    """
    Normalize extension to standard format for comparison.
    
    Args:
        ext: Extension string (e.g., "jpeg", "jpg", "JPG")
        
    Returns:
        Normalized extension (e.g., "jpg")
    """
    ext = ext.lower()
    # Normalize jpeg to jpg
    if ext == "jpeg":
        return "jpg"
    return ext


def validate_uploaded_file(
    file_content: bytes, 
    client_content_type: Optional[str] = None,
    client_filename: Optional[str] = None
) -> Tuple[str, str]:
    """
    Complete validation of an uploaded file.
    
    This function performs all security checks:
    1. Validates file size
    2. Validates magic bytes (real file type)
    3. Validates image integrity with PIL
    4. Verifies file type matches allowed types
    5. Warns if client-provided filename extension doesn't match actual type
    
    IMPORTANT: The client-provided filename extension is NEVER trusted or used.
    The extension is determined solely from the validated MIME type (magic bytes).
    
    Args:
        file_content: Raw file content as bytes
        client_content_type: Content type provided by client (for logging only, not trusted)
        client_filename: Filename provided by client (for logging only, not trusted)
        
    Returns:
        Tuple of (mime_type, file_extension) if valid
        The extension is determined from the validated MIME type, NOT from client_filename
        
    Raises:
        HTTPException: If any validation fails
    """
    # Log client-provided content type for debugging (but don't trust it)
    if client_content_type:
        logger.debug(f"Client provided content-type: {client_content_type}")
    
    # Validate file size first (fast check)
    validate_file_size(file_content)
    
    # Validate file content (magic bytes + PIL)
    # This determines the REAL file type, ignoring any client-provided information
    mime_type, file_extension = validate_file_content(file_content)
    
    # Warn if client content type doesn't match real type
    if client_content_type and client_content_type != mime_type:
        logger.warning(
            f"Content type mismatch: client provided '{client_content_type}', "
            f"but file is actually '{mime_type}'"
        )
    
    # Security: Validate that client filename extension (if provided) matches actual type
    # This is for detection only - we NEVER use the client extension
    if client_filename:
        client_ext = _extract_extension_from_filename(client_filename)
        if client_ext:
            normalized_client_ext = _normalize_extension(client_ext)
            normalized_validated_ext = _normalize_extension(file_extension)
            
            if normalized_client_ext != normalized_validated_ext:
                logger.warning(
                    f"Filename extension mismatch: client provided '{client_ext}' "
                    f"in filename '{client_filename}', but file is actually '{file_extension}'. "
                    f"Client extension is ignored and will not be used."
                )
    
    logger.info(
        f"File validation successful: type={mime_type}, extension={file_extension} "
        f"(determined from magic bytes, client filename extension ignored)"
    )
    
    return mime_type, file_extension
