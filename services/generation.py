import asyncio
import logging
import uuid
from io import BytesIO
from typing import List

import httpx
from PIL import Image

from gemini_pipeline import generate_variant
from schemas.generate import VariantRequest
from services.database import database_service
from services.storage import storage_service
from utils.url_validation import validate_supabase_storage_url
from config import settings

logger = logging.getLogger(__name__)


async def process_generation_background(
    task_id: int,
    variants: List[VariantRequest],
    source_image_url: str,
) -> None:
    """
    Background task to process image generation.

    Steps:
    - Download source image
    - Generate or reuse variants based on configuration
    - Upload previews
    - Update task state and previews
    
    This function has a global timeout to prevent indefinite blocking.
    """
    try:
        # Wrap the entire task with a global timeout
        await asyncio.wait_for(
            _process_generation_internal(task_id, variants, source_image_url),
            timeout=settings.timeout_background_task
        )
    except asyncio.TimeoutError:
        logger.error(f"Background generation task {task_id} exceeded global timeout of {settings.timeout_background_task}s")
        try:
            database_service.update_task_state(task_id, "configured")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Unexpected error in background generation for task {task_id}: {str(e)}")
        try:
            database_service.update_task_state(task_id, "configured")
        except Exception:
            pass


async def _process_generation_internal(
    task_id: int,
    variants: List[VariantRequest],
    source_image_url: str,
) -> None:
    """Internal function that performs the actual generation work."""
    try:
        task_data = database_service.get_task_raw(task_id)
        config = task_data.get("config", {})
        config_variants = config.get("variants", [])
        config_by_hour = {v.get("hour"): v.get("source", "ai") for v in config_variants}

        # Validate source image URL before downloading
        try:
            validate_supabase_storage_url(source_image_url)
        except Exception as e:
            logger.error(f"Invalid source image URL for task {task_id}: {str(e)}")
            try:
                database_service.update_task_state(task_id, "configured")
            except Exception:
                pass
            return

        async with httpx.AsyncClient(timeout=settings.timeout_http_request) as client:
            try:
                # Download with size limit and configured timeouts
                response = await asyncio.wait_for(
                    client.get(
                        source_image_url,
                        follow_redirects=True,
                        timeout=httpx.Timeout(settings.timeout_http_request, connect=settings.timeout_http_connect)
                    ),
                    timeout=settings.timeout_http_request
                )
                response.raise_for_status()
                
                # Check content length if available
                content_length = response.headers.get("content-length")
                if content_length:
                    size = int(content_length)
                    if size > settings.max_download_size:
                        logger.error(f"Download size {size} exceeds maximum {settings.max_download_size}")
                        raise ValueError(f"File size exceeds maximum allowed size")
                
                # Read content with size limit
                image_bytes = response.content
                if len(image_bytes) > settings.max_download_size:
                    logger.error(f"Downloaded content size {len(image_bytes)} exceeds maximum {settings.max_download_size}")
                    raise ValueError(f"File size exceeds maximum allowed size")
                    
            except httpx.HTTPError as e:
                logger.error(f"Failed to download image from {source_image_url}: {str(e)}")
                try:
                    database_service.update_task_state(task_id, "configured")
                except Exception:
                    pass
                return
            except ValueError as e:
                logger.error(f"File size validation failed: {str(e)}")
                try:
                    database_service.update_task_state(task_id, "configured")
                except Exception:
                    pass
                return

        preview_urls = []
        errors = []

        for variant in variants:
            try:
                variant_source = config_by_hour.get(variant.hour, "ai")

                if variant_source == "original":
                    if variant.format == "thumbnail":
                        # Process image with timeout
                        def process_thumbnail():
                            image = Image.open(BytesIO(image_bytes))
                            if image.mode == "RGBA":
                                rgb_image = Image.new("RGB", image.size, (255, 255, 255))
                                rgb_image.paste(image, mask=image.split()[3] if len(image.split()) == 4 else None)
                                image = rgb_image
                            elif image.mode != "RGB":
                                image = image.convert("RGB")
                            image = image.resize((512, 288), Image.Resampling.LANCZOS)
                            img_buffer = BytesIO()
                            image.save(img_buffer, format="PNG")
                            return img_buffer.getvalue()
                        
                        thumbnail_bytes = await asyncio.wait_for(
                            asyncio.to_thread(process_thumbnail),
                            timeout=settings.timeout_image_processing
                        )

                        hour_filename = variant.hour.replace(":", "")
                        preview_name = f"preview_{hour_filename}_{uuid.uuid4().hex[:8]}.png"
                        preview_url = await asyncio.wait_for(
                            asyncio.to_thread(
                                storage_service.upload_to_bucket,
                                file_content=thumbnail_bytes,
                                file_name=preview_name,
                                content_type="image/png",
                                bucket="previews",
                            ),
                            timeout=settings.timeout_storage_operation
                        )
                    else:
                        # Reuse source URL (already validated above)
                        preview_url = source_image_url

                    preview_urls.append({"hour": variant.hour, "url": preview_url})
                    logger.info(f"Using original image for variant hour {variant.hour}")
                else:
                    # Generate variant with timeout
                    generated_image_bytes, auto_uploaded_url = await asyncio.wait_for(
                        asyncio.to_thread(
                            generate_variant,
                            source=image_bytes,
                            hour=variant.hour,
                            format_type=variant.format,
                        ),
                        timeout=settings.timeout_gemini_api
                    )

                    preview_url = None
                    if auto_uploaded_url:
                        # Validate auto-uploaded URL from Gemini pipeline
                        try:
                            validate_supabase_storage_url(auto_uploaded_url)
                            preview_url = auto_uploaded_url
                        except Exception as e:
                            logger.warning(f"Auto-uploaded URL validation failed, uploading manually: {str(e)}")
                            # Fall through to manual upload
                    
                    if not preview_url:
                        hour_filename = variant.hour.replace(":", "")
                        preview_name = f"preview_{hour_filename}_{uuid.uuid4().hex[:8]}.png"
                        preview_url = await asyncio.wait_for(
                            asyncio.to_thread(
                                storage_service.upload_to_bucket,
                                file_content=generated_image_bytes,
                                file_name=preview_name,
                                content_type="image/png",
                                bucket="previews",
                            ),
                            timeout=settings.timeout_storage_operation
                        )

                    preview_urls.append({"hour": variant.hour, "url": preview_url})
                    logger.info(f"Successfully generated and uploaded variant for hour {variant.hour}")

            except ValueError as e:
                error_msg = f"Validation error for variant {variant.hour}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
            except Exception as e:
                error_msg = f"Failed to generate variant for hour {variant.hour}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)

        if len(preview_urls) == 0:
            try:
                database_service.update_task_state(task_id, "configured")
            except Exception:
                pass
            logger.error(f"All variants failed for task {task_id}. Errors: {errors}")
            return

        try:
            database_service.update_task_previews(task_id, preview_urls, "previews_ready")
            logger.info(f"Task {task_id} updated with {len(preview_urls)} preview URLs")
        except Exception as e:
            logger.error(f"Failed to update task with preview URLs: {str(e)}")
            try:
                database_service.update_task_state(task_id, "configured")
            except Exception:
                pass

    except asyncio.TimeoutError:
        logger.error(f"Operation timeout in generation task {task_id}")
        raise
    except Exception as e:
        logger.error(f"Error in generation task {task_id}: {str(e)}")
        raise
