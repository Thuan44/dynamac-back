import asyncio
import logging
import uuid
import time
from datetime import datetime
from io import BytesIO
from typing import List, Dict, Any

import httpx
from PIL import Image

from gemini_pipeline import upscale_image
from services.database import database_service
from services.heic_builder import create_heic_with_multiple_images
from services.storage import storage_service
from utils.url_validation import validate_supabase_storage_url
from config import settings

logger = logging.getLogger(__name__)


async def process_upscale_background(task_id: int) -> None:
    """
    Background task to upscale previews to full-size renders.

    Steps:
    - Fetch previews from urls_previews.variants
    - Download the original source image once (for original variants)
    - For AI variants: download thumbnail and upscale it to 16:9 format
    - Upload to 'upscaled' bucket and store urls_renders
    
    No asyncio timeout wrapper - business timeout logic is handled internally
    based on started_at timestamp and duration checks.
    """
    try:
        await _process_upscale_internal(task_id)
    except asyncio.CancelledError:
        # Preserve CancelledError for shutdown/scale down scenarios
        logger.warning(f"Background upscale task {task_id} was cancelled (shutdown/scale down)")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in background upscale for task {task_id}: {str(e)}")
        # Error handling is done in _process_upscale_internal
        raise


async def _process_upscale_internal(task_id: int) -> None:
    """Internal function that performs the actual upscale work."""
    # Mark task as processing and store started_at
    started_at = database_service.update_task_processing_started(task_id)
    
    try:
        task_data = database_service.get_task_raw(task_id)
        urls_previews = task_data.get("urls_previews", {})
        variants: List[Dict[str, Any]] = urls_previews.get("variants", [])
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

        config_variants = config.get("variants", [])
        config_by_hour = {v.get("hour"): v.get("source", "ai") for v in config_variants}

        # Validate source URL before downloading
        try:
            validate_supabase_storage_url(source_url)
        except Exception as e:
            logger.error(f"Invalid source URL for task {task_id}: {str(e)}")
            database_service.update_task_state(task_id, "previews_ready")
            return

        renders = []
        errors = []
        images_for_heic = []

        async with httpx.AsyncClient(timeout=settings.timeout_http_request) as client:
            try:
                # Download with size limit and configured timeouts
                source_response = await asyncio.wait_for(
                    client.get(
                        source_url,
                        follow_redirects=True,
                        timeout=httpx.Timeout(settings.timeout_http_request, connect=settings.timeout_http_connect)
                    ),
                    timeout=settings.timeout_http_request
                )
                source_response.raise_for_status()
                
                # Check content length if available
                content_length = source_response.headers.get("content-length")
                if content_length:
                    size = int(content_length)
                    if size > settings.max_download_size:
                        logger.error(f"Download size {size} exceeds maximum {settings.max_download_size}")
                        raise ValueError(f"File size exceeds maximum allowed size")
                
                # Read content with size limit
                source_bytes = source_response.content
                if len(source_bytes) > settings.max_download_size:
                    logger.error(f"Downloaded content size {len(source_bytes)} exceeds maximum {settings.max_download_size}")
                    raise ValueError(f"File size exceeds maximum allowed size")
                    
            except httpx.HTTPError as e:
                logger.error(f"Failed to download source image {source_url}: {str(e)}")
                database_service.update_task_state(task_id, "previews_ready")
                return
            except ValueError as e:
                logger.error(f"File size validation failed: {str(e)}")
                database_service.update_task_state(task_id, "previews_ready")
                return

            # Process original image (CPU-bound, no asyncio timeout)
            def process_original_image():
                original_image = Image.open(BytesIO(source_bytes))
                if original_image.mode == "RGBA":
                    rgb_image = Image.new("RGB", original_image.size, (255, 255, 255))
                    rgb_image.paste(original_image, mask=original_image.split()[3] if len(original_image.split()) == 4 else None)
                    original_image = rgb_image
                elif original_image.mode != "RGB":
                    original_image = original_image.convert("RGB")
                original_image = original_image.resize((2560, 1440), Image.Resampling.LANCZOS)
                original_buffer = BytesIO()
                original_image.save(original_buffer, format="PNG")
                return original_buffer.getvalue()
            
            original_resized_bytes = await asyncio.to_thread(process_original_image)

            has_original_variant = any(
                config_by_hour.get(variant.get("hour"), "ai") == "original"
                for variant in variants
                if variant.get("hour")
            )

            if not has_original_variant:
                images_for_heic.append((original_resized_bytes, None))

            for variant in variants:
                hour = variant.get("hour")

                if not hour:
                    errors.append(f"Missing hour for variant: {variant}")
                    continue

                try:
                    variant_source = config_by_hour.get(hour, "ai")

                    if variant_source == "original":
                        render_bytes = original_resized_bytes
                        render_name = f"render_{hour.replace(':', '')}_{uuid.uuid4().hex[:8]}.png"
                        render_url = await asyncio.wait_for(
                            asyncio.to_thread(
                                storage_service.upload_to_bucket,
                                file_content=render_bytes,
                                file_name=render_name,
                                content_type="image/png",
                                bucket="upscaled",
                            ),
                            timeout=settings.timeout_storage_operation
                        )

                        renders.append({"hour": hour, "url": render_url})
                        images_for_heic.append((render_bytes, hour))
                        logger.info(f"Using original resized image for variant hour {hour}")
                    else:
                        preview_url = variant.get("url")
                        if not preview_url:
                            errors.append(f"Missing preview URL for variant hour {hour}")
                            continue

                        # Validate preview URL before downloading
                        try:
                            validate_supabase_storage_url(preview_url)
                        except Exception as e:
                            error_msg = f"Invalid preview URL for variant hour {hour}: {str(e)}"
                            logger.error(error_msg)
                            errors.append(error_msg)
                            continue

                        try:
                            # Download with size limit and configured timeouts
                            preview_response = await asyncio.wait_for(
                                client.get(
                                    preview_url,
                                    follow_redirects=True,
                                    timeout=httpx.Timeout(settings.timeout_http_request, connect=settings.timeout_http_connect)
                                ),
                                timeout=settings.timeout_http_request
                            )
                            preview_response.raise_for_status()
                            
                            # Check content length if available
                            content_length = preview_response.headers.get("content-length")
                            if content_length:
                                size = int(content_length)
                                if size > settings.max_download_size:
                                    raise ValueError(f"File size exceeds maximum allowed size")
                            
                            # Read content with size limit
                            thumbnail_bytes = preview_response.content
                            if len(thumbnail_bytes) > settings.max_download_size:
                                raise ValueError(f"File size exceeds maximum allowed size")
                                
                        except httpx.HTTPError as e:
                            error_msg = f"Failed to download thumbnail from {preview_url} for hour {hour}: {str(e)}"
                            logger.error(error_msg)
                            errors.append(error_msg)
                            continue
                        except ValueError as e:
                            error_msg = f"File size validation failed for preview {preview_url} hour {hour}: {str(e)}"
                            logger.error(error_msg)
                            errors.append(error_msg)
                            continue

                        # Upscale image (CPU-bound, no asyncio timeout)
                        render_bytes = await asyncio.to_thread(
                            upscale_image,
                            source=thumbnail_bytes,
                            format_type="16:9"
                        )
                        render_name = f"render_{hour.replace(':', '')}_{uuid.uuid4().hex[:8]}.png"
                        render_url = await asyncio.wait_for(
                            asyncio.to_thread(
                                storage_service.upload_to_bucket,
                                file_content=render_bytes,
                                file_name=render_name,
                                content_type="image/png",
                                bucket="upscaled",
                            ),
                            timeout=settings.timeout_storage_operation
                        )

                        renders.append({"hour": hour, "url": render_url})
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

        heic_url = None
        heic_created = False
        try:
            if len(images_for_heic) > 0:
                logger.info(f"Creating HEIC file with {len(images_for_heic)} images for task {task_id}")
                # Create HEIC (CPU-bound, no asyncio timeout)
                # Measure actual duration for business timeout logic
                heic_start_time = time.time()
                heic_bytes = create_heic_with_multiple_images(images_for_heic)
                heic_duration = time.time() - heic_start_time
                logger.info(f"HEIC file created successfully in {heic_duration:.2f}s, size: {len(heic_bytes)} bytes")
                
                # Business timeout check: verify if duration exceeded expected timeout
                if heic_duration > settings.timeout_heic_creation:
                    logger.warning(
                        f"HEIC creation for task {task_id} took {heic_duration:.2f}s, "
                        f"exceeding expected timeout of {settings.timeout_heic_creation}s, "
                        f"but operation completed successfully"
                    )

                heic_name = f"dynamic_{task_id}_{uuid.uuid4().hex[:8]}.heic"
                logger.info(f"Uploading HEIC file to dynamics bucket: {heic_name}")
                heic_url = await asyncio.wait_for(
                    asyncio.to_thread(
                        storage_service.upload_to_bucket,
                        file_content=heic_bytes,
                        file_name=heic_name,
                        content_type="image/heic",
                        bucket="dynamics",
                    ),
                    timeout=settings.timeout_storage_operation
                )
                logger.info(f"Successfully created and uploaded HEIC file with {len(images_for_heic)} images to dynamics bucket: {heic_url}")
                heic_created = True

                try:
                    database_service.update_task_final_url(task_id, heic_url)
                    logger.info(f"Task {task_id} final URL updated: {heic_url}")
                except Exception as url_error:
                    logger.error(f"Failed to update final URL for task {task_id}: {str(url_error)}")
            else:
                logger.warning(f"No images to include in HEIC file for task {task_id}")
        except Exception as heic_error:
            logger.error(f"Failed to create or upload HEIC file for task {task_id}: {str(heic_error)}", exc_info=True)
            heic_created = False
            # Check if this is a timeout scenario based on started_at
            try:
                task_started_at = database_service.get_task_started_at(task_id)
                if task_started_at:
                    elapsed = (datetime.utcnow() - task_started_at).total_seconds()
                    if elapsed > settings.timeout_heic_creation:
                        logger.error(
                            f"HEIC creation failed for task {task_id} after {elapsed:.2f}s, "
                            f"exceeding timeout of {settings.timeout_heic_creation}s"
                        )
                        try:
                            database_service.update_task_state(task_id, "timeout")
                        except Exception:
                            pass
                        return
            except Exception:
                pass
            # If not a timeout, mark as failed_heic
            try:
                database_service.update_task_state(task_id, "failed_heic")
            except Exception:
                pass

        if not heic_created:
            logger.error(f"Cannot proceed to renders_ready for task {task_id}: HEIC file was not created successfully")
            # State already updated above if needed
            return

        try:
            database_service.update_task_renders(task_id, renders, "renders_ready")
            logger.info(f"Task {task_id} updated with {len(renders)} renders and state set to renders_ready")
        except Exception as e:
            logger.error(f"Failed to update task with renders: {str(e)}")
            try:
                database_service.update_task_state(task_id, "previews_ready")
            except Exception:
                pass
    except asyncio.CancelledError:
        # Preserve CancelledError for shutdown/scale down scenarios
        logger.warning(f"Upscale task {task_id} was cancelled (shutdown/scale down)")
        try:
            database_service.update_task_state(task_id, "cancelled")
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"Error in upscale task {task_id}: {str(e)}")
        # Check if this is a timeout scenario based on started_at
        try:
            task_started_at = database_service.get_task_started_at(task_id)
            if task_started_at:
                elapsed = (datetime.utcnow() - task_started_at).total_seconds()
                if elapsed > settings.timeout_background_task:
                    logger.error(
                        f"Upscale task {task_id} failed after {elapsed:.2f}s, "
                        f"exceeding background task timeout of {settings.timeout_background_task}s"
                    )
                    try:
                        database_service.update_task_state(task_id, "timeout")
                    except Exception:
                        pass
                    return
        except Exception:
            pass
        # For other errors, try to reset to previous state
        try:
            database_service.update_task_state(task_id, "previews_ready")
        except Exception:
            pass
