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
    """
    try:
        task_data = database_service.get_task_raw(task_id)
        config = task_data.get("config", {})
        config_variants = config.get("variants", [])
        config_by_hour = {v.get("hour"): v.get("source", "ai") for v in config_variants}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(source_image_url)
                response.raise_for_status()
                image_bytes = response.content
            except httpx.HTTPError as e:
                logger.error(f"Failed to download image from {source_image_url}: {str(e)}")
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
                        thumbnail_bytes = img_buffer.getvalue()

                        hour_filename = variant.hour.replace(":", "")
                        preview_name = f"preview_{hour_filename}_{uuid.uuid4().hex[:8]}.png"
                        preview_url = storage_service.upload_to_bucket(
                            file_content=thumbnail_bytes,
                            file_name=preview_name,
                            content_type="image/png",
                            bucket="previews",
                        )
                    else:
                        preview_url = source_image_url

                    preview_urls.append({"hour": variant.hour, "url": preview_url})
                    logger.info(f"Using original image for variant hour {variant.hour}")
                else:
                    generated_image_bytes, auto_uploaded_url = generate_variant(
                        source=image_bytes,
                        hour=variant.hour,
                        format_type=variant.format,
                    )

                    if auto_uploaded_url:
                        preview_url = auto_uploaded_url
                    else:
                        hour_filename = variant.hour.replace(":", "")
                        preview_name = f"preview_{hour_filename}_{uuid.uuid4().hex[:8]}.png"
                        preview_url = storage_service.upload_to_bucket(
                            file_content=generated_image_bytes,
                            file_name=preview_name,
                            content_type="image/png",
                            bucket="previews",
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

    except Exception as e:
        logger.error(f"Unexpected error in background generation for task {task_id}: {str(e)}")
        try:
            database_service.update_task_state(task_id, "configured")
        except Exception:
            pass
