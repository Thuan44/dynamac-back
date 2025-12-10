import logging
import math
import plistlib
import struct
from datetime import datetime
from typing import Literal, List, Tuple, Optional
from fastapi import APIRouter, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field, field_validator
import httpx
from services.database import database_service
from services.storage import storage_service
from gemini_pipeline import generate_variant, upscale_image
import uuid
from io import BytesIO
from PIL import Image
from pillow_heif import register_heif_opener, HeifFile

logger = logging.getLogger(__name__)

# Register HEIC support in Pillow
register_heif_opener()

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
        # Get task configuration to check which variants are "original" vs "ai"
        task_data = database_service.get_task_raw(task_id)
        config = task_data.get("config", {})
        config_variants = config.get("variants", [])
        
        # Create a mapping of hour -> source type from config
        config_by_hour = {v.get("hour"): v.get("source", "ai") for v in config_variants}
        
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
                variant_source = config_by_hour.get(variant.hour, "ai")
                
                # If source is "original", use the original image URL or create thumbnail
                if variant_source == "original":
                    if variant.format == "thumbnail":
                        # Create thumbnail from original image
                        image = Image.open(BytesIO(image_bytes))
                        # Convert to RGB if necessary
                        if image.mode == "RGBA":
                            rgb_image = Image.new("RGB", image.size, (255, 255, 255))
                            rgb_image.paste(image, mask=image.split()[3] if len(image.split()) == 4 else None)
                            image = rgb_image
                        elif image.mode != "RGB":
                            image = image.convert("RGB")
                        
                        # Resize to thumbnail dimensions (512x288)
                        image = image.resize((512, 288), Image.Resampling.LANCZOS)
                        
                        # Convert to bytes
                        img_buffer = BytesIO()
                        image.save(img_buffer, format="PNG")
                        thumbnail_bytes = img_buffer.getvalue()
                        
                        # Upload thumbnail
                        hour_filename = variant.hour.replace(":", "")
                        preview_name = f"preview_{hour_filename}_{uuid.uuid4().hex[:8]}.png"
                        preview_url = storage_service.upload_to_bucket(
                            file_content=thumbnail_bytes,
                            file_name=preview_name,
                            content_type="image/png",
                            bucket="previews"
                        )
                    else:
                        # For 16:9 format, use original source URL directly
                        preview_url = source_image_url
                    
                    preview_urls.append({
                        "hour": variant.hour,
                        "url": preview_url
                    })
                    
                    logger.info(f"Using original image for variant hour {variant.hour}")
                else:
                    # Generate variant using Gemini for "ai" source
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


def hour_to_decimal(hour: str) -> float:
    """
    Convert hour in HH:MM format to decimal hours.
    
    Args:
        hour: Hour in HH:MM format (e.g., "14:30")
        
    Returns:
        Decimal hours (e.g., 14.5 for 14:30)
    """
    hour_parts = hour.split(":")
    hour_int = int(hour_parts[0])
    minute_int = int(hour_parts[1])
    
    # Convert to decimal hours
    decimal_hours = hour_int + (minute_int / 60.0)
    
    return decimal_hours


def create_dynamic_desktop_plist(images_with_hours: List[tuple]) -> bytes:
    """
    Create a Plist XML with 'time' key containing array of dictionaries.
    Each dictionary has 'start' (decimal hours) and 'image' (index).
    
    Args:
        images_with_hours: List of tuples (image_bytes, hour) where:
            - hour: Hour in HH:MM format (None for original image)
        
    Returns:
        Binary Plist bytes
    """
    time_entries = []
    
    for idx, (image_bytes, hour) in enumerate(images_with_hours):
        if hour:
            # Convert hour to decimal
            decimal_hours = hour_to_decimal(hour)
            
            # Create entry for this image
            entry = {
                "start": decimal_hours,
                "image": idx
            }
            time_entries.append(entry)
    
    # Sort by start time to ensure ascending order
    time_entries.sort(key=lambda x: x["start"])
    
    # Create Plist structure
    plist_data = {
        "time": time_entries
    }
    
    # Convert to binary Plist
    plist_bytes = plistlib.dumps(plist_data, fmt=plistlib.FMT_BINARY)
    
    logger.info(f"Created Dynamic Desktop Plist with {len(time_entries)} time entries")
    
    return plist_bytes


def find_uuid_box(heic_data: bytes, uuid_bytes: bytes) -> Optional[Tuple[int, int]]:
    """
    Find a UUID box in HEIC/ISO BMFF data.
    
    Args:
        heic_data: HEIC file bytes
        uuid_bytes: UUID bytes to search for (16 bytes)
        
    Returns:
        Tuple (offset, size) of the UUID box if found, None otherwise
    """
    def parse_box(start_pos: int, max_pos: int) -> Optional[Tuple[int, int]]:
        """Recursively parse boxes to find UUID box."""
        pos = start_pos
        while pos < max_pos - 8:
            # Read box size (4 bytes, big-endian)
            if pos + 4 > max_pos:
                break
            box_size = struct.unpack(">I", heic_data[pos:pos+4])[0]
            
            # Read box type (4 bytes)
            if pos + 8 > max_pos:
                break
            box_type = heic_data[pos+4:pos+8]
            
            # Handle large size (size == 1 means extended size follows)
            header_size = 8
            if box_size == 1:
                if pos + 16 > max_pos:
                    break
                box_size = struct.unpack(">Q", heic_data[pos+8:pos+16])[0]
                header_size = 16
            elif box_size == 0:
                # Box extends to end of container
                box_size = max_pos - pos
            
            # Check if this is a UUID box
            if box_type == b"uuid":
                uuid_start = pos + header_size
                if uuid_start + 16 <= max_pos and uuid_start + 16 <= pos + box_size:
                    box_uuid = heic_data[uuid_start:uuid_start+16]
                    if box_uuid == uuid_bytes:
                        return (pos, box_size)
            
            # Recursively search in container boxes
            if box_type in [b"meta", b"moov", b"trak", b"mdia", b"minf", b"stbl", b"iloc", b"iinf", b"iref"]:
                # Container box - search inside
                inner_start = pos + header_size
                inner_end = min(pos + box_size, max_pos)
                if inner_start < inner_end:
                    result = parse_box(inner_start, inner_end)
                    if result:
                        return result
            
            # Move to next box
            if box_size == 0:
                break
            pos += box_size
        
        return None
    
    return parse_box(0, len(heic_data))


def create_uuid_box(uuid_bytes: bytes, plist_data: bytes) -> bytes:
    """
    Create a UUID box with the given UUID and data.
    
    Args:
        uuid_bytes: UUID bytes (16 bytes)
        plist_data: Data to store in the box
        
    Returns:
        Complete UUID box bytes
    """
    # Box structure: size (4) + type (4) + uuid (16) + data
    box_type = b"uuid"
    data_size = len(plist_data)
    total_size = 8 + 16 + data_size
    
    # Create box
    box = struct.pack(">I", total_size)  # size
    box += box_type  # type
    box += uuid_bytes  # uuid
    box += plist_data  # data
    
    return box


def inject_uuid_box(heic_data: bytes, uuid_bytes: bytes, plist_data: bytes) -> bytes:
    """
    Inject or replace a UUID box in HEIC/ISO BMFF data.
    
    Args:
        heic_data: Original HEIC file bytes
        uuid_bytes: UUID bytes (16 bytes) for Dynamic Desktop
        plist_data: Plist binary data to inject
        
    Returns:
        Modified HEIC file bytes with UUID box injected/replaced
    """
    # Find existing UUID box
    uuid_box_info = find_uuid_box(heic_data, uuid_bytes)
    
    # Create new UUID box
    new_uuid_box = create_uuid_box(uuid_bytes, plist_data)
    
    if uuid_box_info:
        # Replace existing box
        offset, old_size = uuid_box_info
        
        # Calculate size difference
        new_size = len(new_uuid_box)
        size_diff = new_size - old_size
        
        if size_diff == 0:
            # Same size, simple replacement
            result = heic_data[:offset] + new_uuid_box + heic_data[offset + old_size:]
        else:
            # Different size, need to update box sizes
            # Replace the box
            result = heic_data[:offset] + new_uuid_box + heic_data[offset + old_size:]
            # Update the size field of the replaced box
            result = result[:offset] + struct.pack(">I", new_size) + result[offset + 4:]
            # Note: We may need to update parent container sizes, but for simplicity
            # we'll assume the file structure can accommodate the size change
        
        logger.info(f"Replaced existing UUID box at offset {offset} (old size: {old_size}, new size: {new_size})")
    else:
        # Insert new UUID box in 'meta' box if it exists, otherwise after 'ftyp'
        # Find 'meta' box position
        meta_pos = -1
        meta_end = -1
        
        pos = 0
        while pos < len(heic_data) - 8:
            if pos + 8 > len(heic_data):
                break
            box_size = struct.unpack(">I", heic_data[pos:pos+4])[0]
            box_type = heic_data[pos+4:pos+8]
            
            header_size = 8
            if box_size == 1:
                if pos + 16 > len(heic_data):
                    break
                box_size = struct.unpack(">Q", heic_data[pos+8:pos+16])[0]
                header_size = 16
            elif box_size == 0:
                break
            
            if box_type == b"meta":
                meta_pos = pos
                meta_end = pos + box_size
                break
            
            pos += box_size
        
        if meta_pos != -1 and meta_end != -1:
            # Insert inside meta box, after its header
            # Meta box header: size (4) + type (4) + version/flags (4) = 12 bytes
            meta_header_size = 12
            insert_pos = meta_pos + meta_header_size
            
            # Insert UUID box inside meta box
            result = heic_data[:insert_pos] + new_uuid_box + heic_data[insert_pos:]
            
            # Update meta box size
            new_meta_size = (meta_end - meta_pos) + len(new_uuid_box)
            result = result[:meta_pos] + struct.pack(">I", new_meta_size) + result[meta_pos + 4:]
            
            logger.info(f"Inserted new UUID box inside meta box at offset {insert_pos}")
        else:
            # Find 'ftyp' box and insert after it
            ftyp_pos = heic_data.find(b"ftyp")
            if ftyp_pos != -1 and ftyp_pos + 4 <= len(heic_data):
                # Read ftyp box size
                ftyp_size = struct.unpack(">I", heic_data[ftyp_pos:ftyp_pos+4])[0]
                if ftyp_size == 0:
                    ftyp_size = 8  # Minimum size
                insert_pos = ftyp_pos + ftyp_size
                result = heic_data[:insert_pos] + new_uuid_box + heic_data[insert_pos:]
                logger.info(f"Inserted new UUID box after ftyp box at offset {insert_pos}")
            else:
                # Fallback: insert at position 8 (after potential initial box)
                result = heic_data[:8] + new_uuid_box + heic_data[8:]
                logger.info("Inserted new UUID box at offset 8 (fallback)")
    
    return result


def create_heic_with_multiple_images(images_with_hours: List[tuple]) -> bytes:
    """
    Create a single HEIC file containing multiple images with Dynamic Desktop Plist metadata
    injected into a UUID box for macOS dynamic wallpaper time-shifting.
    
    Args:
        images_with_hours: List of tuples (image_bytes, hour) where:
            - image_bytes: PNG image bytes to convert to HEIC
            - hour: Hour in HH:MM format to embed as temporal metadata (None for original image)
        
    Returns:
        HEIC file bytes containing all images with embedded Dynamic Desktop Plist metadata in UUID box
    """
    try:
        # Validate and prepare images with hours
        # Filter out None hours and create sorted list for validation
        time_entries_data = []
        for idx, (image_bytes, hour) in enumerate(images_with_hours):
            if hour:
                decimal_hours = hour_to_decimal(hour)
                time_entries_data.append((idx, decimal_hours, hour))
        
        # Sort by start time to ensure ascending order
        time_entries_data.sort(key=lambda x: x[1])
        
        # Verify image indices match
        for i, (idx, decimal_hours, hour) in enumerate(time_entries_data):
            if idx >= len(images_with_hours):
                raise ValueError(f"Image index {idx} is out of range (total images: {len(images_with_hours)})")
            logger.debug(f"Time entry {i}: image={idx}, start={decimal_hours:.2f} ({hour})")
        
        # Create HeifFile and add all images
        heif_file = HeifFile()
        
        for idx, (image_bytes, hour) in enumerate(images_with_hours):
            # Open image from bytes
            image = Image.open(BytesIO(image_bytes))
            
            # Convert RGBA to RGB if necessary (HEIC doesn't support alpha channel well)
            if image.mode == "RGBA":
                rgb_image = Image.new("RGB", image.size, (255, 255, 255))
                rgb_image.paste(image, mask=image.split()[3] if len(image.split()) == 4 else None)
                image = rgb_image
            elif image.mode != "RGB":
                image = image.convert("RGB")
            
            # Add image to HeifFile
            heif_file.add_from_pillow(image)
            
            logger.debug(f"Added image {idx + 1} to HEIC file (hour: {hour or 'original'})")
        
        # Save to HEIC format in memory
        heic_buffer = BytesIO()
        heif_file.save(heic_buffer, quality=90)
        heic_buffer.seek(0)
        heic_bytes = heic_buffer.getvalue()
        
        # Create Dynamic Desktop Plist with 'time' key
        if time_entries_data:
            plist_bytes = create_dynamic_desktop_plist(images_with_hours)
            
            # UUID for macOS Dynamic Desktop: B1B2B3B4-B5B6-B7B8-B9BA-BBBBBCBDBEBF
            dynamic_desktop_uuid = bytes.fromhex("B1B2B3B4B5B6B7B8B9BABBBBBCBDBEBF")
            
            # Inject UUID box with Plist data
            heic_bytes = inject_uuid_box(heic_bytes, dynamic_desktop_uuid, plist_bytes)
            logger.info(f"Injected Dynamic Desktop Plist into UUID box with {len(time_entries_data)} time entries")
        else:
            logger.warning("No time entries found, skipping Plist injection")
        
        return heic_bytes
        
    except Exception as e:
        logger.error(f"Failed to create HEIC with multiple images: {str(e)}", exc_info=True)
        raise


async def process_upscale_background(task_id: int) -> None:
    """
    Background task to upscale previews to full-size renders.
    
    Steps:
    - Fetch previews from urls_previews.variants
    - Download the original source image once (for original variants)
    - For AI variants: download thumbnail and upscale it to 16:9 format
    - Upload to 'upscaled' bucket and store urls_renders
    """
    try:
        task_data = database_service.get_task_raw(task_id)
        urls_previews = task_data.get("urls_previews", {})
        variants = urls_previews.get("variants", [])
        source_url = task_data.get("url_source")
        config = task_data.get("config", {})
        
        if not variants:
            logger.error(f"No previews found to upscale for task {task_id}")
            database_service.update_task_state(task_id, "previews_ready")
            return
        if not source_url:
            logger.error(f"No source URL found to upscale for task {task_id}")
            database_service.update_task_state(task_id, "previews_ready")
            return
        
        # Create a mapping of hour -> source type from config
        config_variants = config.get("variants", [])
        config_by_hour = {v.get("hour"): v.get("source", "ai") for v in config_variants}
        
        renders = []
        errors = []
        images_for_heic = []  # List to collect all images for HEIC file: [(image_bytes, hour), ...]
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                source_response = await client.get(source_url)
                source_response.raise_for_status()
                source_bytes = source_response.content
            except httpx.HTTPError as e:
                logger.error(f"Failed to download source image {source_url}: {str(e)}")
                database_service.update_task_state(task_id, "previews_ready")
                return
            
            # Resize original image to 16:9 (1920x1080) for HEIC consistency
            original_image = Image.open(BytesIO(source_bytes))
            # Convert to RGB if necessary
            if original_image.mode == "RGBA":
                rgb_image = Image.new("RGB", original_image.size, (255, 255, 255))
                rgb_image.paste(original_image, mask=original_image.split()[3] if len(original_image.split()) == 4 else None)
                original_image = rgb_image
            elif original_image.mode != "RGB":
                original_image = original_image.convert("RGB")
            
            # Resize to 16:9 format (1920x1080)
            original_image = original_image.resize((1920, 1080), Image.Resampling.LANCZOS)
            
            # Convert resized original to bytes
            original_buffer = BytesIO()
            original_image.save(original_buffer, format="PNG")
            original_resized_bytes = original_buffer.getvalue()
            
            # Check if any variant uses "original" source to avoid duplicate
            has_original_variant = any(
                config_by_hour.get(variant.get("hour"), "ai") == "original"
                for variant in variants
                if variant.get("hour")
            )
            
            # Add original image first only if no variant uses it (to avoid duplicate)
            if not has_original_variant:
                images_for_heic.append((original_resized_bytes, None))
            
            for variant in variants:
                hour = variant.get("hour")
                
                if not hour:
                    errors.append(f"Missing hour for variant: {variant}")
                    continue
                
                try:
                    variant_source = config_by_hour.get(hour, "ai")
                    
                    # If source is "original", use the resized original image
                    if variant_source == "original":
                        render_bytes = original_resized_bytes
                        
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
                        
                        # Add variant image with its hour for HEIC file
                        images_for_heic.append((render_bytes, hour))
                        
                        logger.info(f"Using original resized image for variant hour {hour}")
                    else:
                        # Upscale thumbnail using Gemini for "ai" source
                        preview_url = variant.get("url")
                        if not preview_url:
                            errors.append(f"Missing preview URL for variant hour {hour}")
                            continue
                        
                        # Download thumbnail
                        try:
                            preview_response = await client.get(preview_url)
                            preview_response.raise_for_status()
                            thumbnail_bytes = preview_response.content
                        except httpx.HTTPError as e:
                            error_msg = f"Failed to download thumbnail from {preview_url} for hour {hour}: {str(e)}"
                            logger.error(error_msg)
                            errors.append(error_msg)
                            continue
                        
                        # Upscale thumbnail to 16:9 format
                        render_bytes = upscale_image(
                            source=thumbnail_bytes,
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
                        
                        # Add variant image with its hour for HEIC file
                        images_for_heic.append((render_bytes, hour))
                        
                        logger.info(f"Successfully upscaled thumbnail to render for hour {hour}")
                        
                except Exception as e:
                    error_msg = f"Failed to upscale variant {hour}: {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)
        
        if len(renders) == 0:
            logger.error(f"All upscales failed for task {task_id}. Errors: {errors}")
            database_service.update_task_state(task_id, "previews_ready")
            return
        
        # Create single HEIC file with all images (original + variants) and store in dynamics bucket
        heic_url = None
        heic_created = False
        try:
            if len(images_for_heic) > 0:
                logger.info(f"Creating HEIC file with {len(images_for_heic)} images for task {task_id}")
                heic_bytes = create_heic_with_multiple_images(images_for_heic)
                logger.info(f"HEIC file created successfully, size: {len(heic_bytes)} bytes")
                
                heic_name = f"dynamic_{task_id}_{uuid.uuid4().hex[:8]}.heic"
                logger.info(f"Uploading HEIC file to dynamics bucket: {heic_name}")
                heic_url = storage_service.upload_to_bucket(
                    file_content=heic_bytes,
                    file_name=heic_name,
                    content_type="image/heic",
                    bucket="dynamics"
                )
                logger.info(f"Successfully created and uploaded HEIC file with {len(images_for_heic)} images to dynamics bucket: {heic_url}")
                heic_created = True
                
                # Update url_finale in database with HEIC file URL
                try:
                    database_service.update_task_final_url(task_id, heic_url)
                    logger.info(f"Task {task_id} final URL updated: {heic_url}")
                except Exception as url_error:
                    logger.error(f"Failed to update final URL for task {task_id}: {str(url_error)}")
                    # This is not critical, we can continue
            else:
                logger.warning(f"No images to include in HEIC file for task {task_id}")
        except Exception as heic_error:
            logger.error(f"Failed to create or upload HEIC file for task {task_id}: {str(heic_error)}", exc_info=True)
            # Don't proceed to renders_ready if HEIC creation failed
            heic_created = False
        
        # Only update to renders_ready if HEIC was successfully created
        if not heic_created:
            logger.error(f"Cannot proceed to renders_ready for task {task_id}: HEIC file was not created successfully")
            try:
                database_service.update_task_state(task_id, "previews_ready")
            except Exception as state_error:
                logger.error(f"Failed to reset task state for task {task_id}: {str(state_error)}")
            return
        
        try:
            database_service.update_task_renders(task_id, renders, "renders_ready")
            logger.info(f"Task {task_id} updated with {len(renders)} renders and state set to renders_ready")
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

