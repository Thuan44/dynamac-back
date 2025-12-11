import logging
import uuid
from io import BytesIO
from typing import List, Dict, Any

import httpx
from PIL import Image

from gemini_pipeline import upscale_image
from services.database import database_service
from services.heic_builder import create_heic_with_multiple_images
from services.storage import storage_service

logger = logging.getLogger(__name__)


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

        renders = []
        errors = []
        images_for_heic = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                source_response = await client.get(source_url)
                source_response.raise_for_status()
                source_bytes = source_response.content
            except httpx.HTTPError as e:
                logger.error(f"Failed to download source image {source_url}: {str(e)}")
                database_service.update_task_state(task_id, "previews_ready")
                return

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
            original_resized_bytes = original_buffer.getvalue()

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
                        render_url = storage_service.upload_to_bucket(
                            file_content=render_bytes,
                            file_name=render_name,
                            content_type="image/png",
                            bucket="upscaled",
                        )

                        renders.append({"hour": hour, "url": render_url})
                        images_for_heic.append((render_bytes, hour))
                        logger.info(f"Using original resized image for variant hour {hour}")
                    else:
                        preview_url = variant.get("url")
                        if not preview_url:
                            errors.append(f"Missing preview URL for variant hour {hour}")
                            continue

                        try:
                            preview_response = await client.get(preview_url)
                            preview_response.raise_for_status()
                            thumbnail_bytes = preview_response.content
                        except httpx.HTTPError as e:
                            error_msg = f"Failed to download thumbnail from {preview_url} for hour {hour}: {str(e)}"
                            logger.error(error_msg)
                            errors.append(error_msg)
                            continue

                        render_bytes = upscale_image(source=thumbnail_bytes, format_type="16:9")
                        render_name = f"render_{hour.replace(':', '')}_{uuid.uuid4().hex[:8]}.png"
                        render_url = storage_service.upload_to_bucket(
                            file_content=render_bytes,
                            file_name=render_name,
                            content_type="image/png",
                            bucket="upscaled",
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
                heic_bytes = create_heic_with_multiple_images(images_for_heic)
                logger.info(f"HEIC file created successfully, size: {len(heic_bytes)} bytes")

                heic_name = f"dynamic_{task_id}_{uuid.uuid4().hex[:8]}.heic"
                logger.info(f"Uploading HEIC file to dynamics bucket: {heic_name}")
                heic_url = storage_service.upload_to_bucket(
                    file_content=heic_bytes,
                    file_name=heic_name,
                    content_type="image/heic",
                    bucket="dynamics",
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
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Unexpected error in background upscale for task {task_id}: {str(e)}")
        try:
            database_service.update_task_state(task_id, "previews_ready")
        except Exception:
            pass
