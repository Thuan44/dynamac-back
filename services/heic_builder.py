import logging
from io import BytesIO
from typing import List, Tuple, Optional

from PIL import Image
from pillow_heif import HeifFile, register_heif_opener

from utils.time_metadata import create_time_based_plist, create_xmp_metadata

# Ensure HEIC support
register_heif_opener()

logger = logging.getLogger(__name__)


def create_heic_with_multiple_images(images_with_hours: List[Tuple[bytes, Optional[str]]]) -> bytes:
    """
    Create a HEIC file with multiple images and optional time-based metadata.

    Args:
        images_with_hours: list of tuples (image_bytes, hour) where hour can be None.

    Returns:
        HEIC file content as bytes.
    """
    try:
        heif_file = HeifFile()
        total_images = len(images_with_hours)

        for idx, (image_bytes, hour) in enumerate(images_with_hours):
            if idx >= total_images:
                logger.error(f"Index mismatch: Image {idx} requested but only {total_images} in HEIC")
                raise ValueError("Index mismatch in HEIC generation")
            try:
                image_buffer = BytesIO(image_bytes)
                image = Image.open(image_buffer)
                image.load()

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

        xmp_data = None
        if len(images_with_hours) > 1:
            try:
                plist_bytes = create_time_based_plist(images_with_hours)
                xmp_data = create_xmp_metadata(plist_bytes)
                logger.info("Generated Time-Based XMP metadata")
            except Exception as e:
                logger.error(f"Failed to create metadata: {str(e)}")

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
