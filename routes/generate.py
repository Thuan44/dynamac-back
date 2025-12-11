import logging
import plistlib
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
import base64

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

def get_normalized_time(hour_str: str) -> float:
    """Convert HH:MM to normalized value between 0.0 and 1.0."""
    if not hour_str:
        return 0.0
        
    parts = hour_str.split(":")
    h = int(parts[0])
    m = int(parts[1])
    
    total_seconds = (h * 3600) + (m * 60)
    
    return total_seconds / 86400.0

def create_time_based_plist(images_with_hours: List[tuple]) -> bytes:
    ti_entries = []
    temp_entries = []
    
    for idx, (img_data, hour) in enumerate(images_with_hours):
        if hour:
            norm_time = float(get_normalized_time(hour))
            temp_entries.append((int(idx), norm_time))
    
    temp_entries.sort(key=lambda x: x[1])
    
    for img_idx, norm_time in temp_entries:
        ti_entries.append({
            "i": img_idx, 
            "t": norm_time
        })

    light_index = min(temp_entries, key=lambda x: abs(x[1] - 0.5))[0]
    
    def dist_to_midnight(t):
        return min(abs(t - 0.0), abs(t - 1.0))
    dark_index = min(temp_entries, key=lambda x: dist_to_midnight(x[1]))[0]

    plist_data = {
        "ti": ti_entries,
        "l": int(light_index),
        "d": int(dark_index)
    }
    
    logger.info(f"Plist Data: {plist_data}")
    
    return plistlib.dumps(plist_data, fmt=plistlib.FMT_BINARY)

def create_xmp_metadata(plist_bytes: bytes) -> bytes:
    """
    Build XMP XML expected by macOS for time-based dynamic wallpapers.
    """
    
    plist_b64 = base64.b64encode(plist_bytes).decode('ascii').replace('\n', '')
    
    xmp_content = f"""<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 5.4.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:apple_desktop="http://ns.apple.com/namespace/1.0/">
   <apple_desktop:h24>{plist_b64}</apple_desktop:h24>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""

    return xmp_content.encode('utf-8')

def create_heic_with_multiple_images(images_with_hours: List[tuple]) -> bytes:
    try:
        # Initialize HEIF file
        heif_file = HeifFile()
        
        # Safety verification
        total_images = len(images_with_hours)

        # Add images
        for idx, (image_bytes, hour) in enumerate(images_with_hours):
            # Guard against mismatched entries versus actual images
            if idx >= total_images:
                logger.error(f"Index mismatch: Image {idx} requested but only {total_images} in HEIC")
                raise ValueError("Index mismatch in HEIC generation")
            try:
                image_buffer = BytesIO(image_bytes)
                image = Image.open(image_buffer)
                image.load()
                
                # Standard RGB conversion
                if image.mode == "RGBA":
                    rgb_image = Image.new("RGB", image.size, (255, 255, 255))
                    rgb_image.paste(image, mask=image.split()[3])
                    image = rgb_image
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                
                heif_file.add_from_pillow(image)
                
            except Exception as e:
                logger.error(f"Failed to add image {idx}: {str(e)}")
                raise

        # Generate metadata based on hour
        xmp_data = None
        if len(images_with_hours) > 1:
            try:
                # 1. Use the new create_time_based_plist function
                plist_bytes = create_time_based_plist(images_with_hours)
                
                # 2. Wrap it in XMP (same function as before)
                xmp_data = create_xmp_metadata(plist_bytes)
                
                logger.info(f"Generated Time-Based XMP metadata")
            except Exception as e:
                logger.error(f"Failed to create metadata: {str(e)}")

        # Save file
        heic_buffer = BytesIO()
        save_args = {"quality": 90}
        
        if xmp_data:
            save_args["xmp"] = xmp_data

        heif_file.save(heic_buffer, **save_args)
        
        heic_buffer.seek(0)
        return heic_buffer.getvalue()

    except Exception as e:
        logger.error(f"Failed to create HEIC: {str(e)}", exc_info=True)
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

