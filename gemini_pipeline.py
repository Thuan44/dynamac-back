import base64
import io
import logging
import os
from typing import Literal, Union
from PIL import Image
from google import genai
from google.genai import types
from config import settings

logger = logging.getLogger(__name__)

# Format dimensions
FORMAT_DIMENSIONS = {
    "thumbnail": (512, 288),  # 16:9 ratio for thumbnails
    "16:9": (1920, 1080),     # Full HD 16:9 format
}

# Base prompt for image modification
PROMPT_BASE = """
Transform this image into a strictly preserved variant.

Do not modify any element of the scene, any object, any texture, any local color, any shape, any angle.

Reproduce the image identically.

Only the lighting may change.

Adjust exclusively:
- global brightness
- sun exposure
- directional light intensity
- balance between direct and diffuse light

No other modification.

No additions, no removals, no stylization.

No alteration of sharpness or depth.

Absolute respect for the original composition except for the sky if visible and any clouds.

Time-based atmosphere guidelines:
If the time is between 21:00 and 05:00, the atmosphere must be nighttime with dark sky, reduced visibility, and moonlight or artificial lighting.
If the time is between 05:00 and 09:00, the atmosphere must be sunrise with warm golden tones, soft light, and dawn colors in the sky.
If the time is between 09:00 and 17:00, the atmosphere must be full daylight with bright sun, strong direct lighting, and clear visibility.
If the time is between 17:00 and 21:00, the atmosphere must be sunset with warm orange and pink tones, golden hour lighting, and dusk colors in the sky.

The image is intended as a desktop/mac wallpaper, so it must be visually striking and aesthetically impactful, with amplified light dynamics if needed.

Apply the lighting adjustment based on the provided display time.
"""


def generate_variant(
    source: Union[str, bytes], 
    hour: str, 
    format_type: Literal["thumbnail", "16:9"] = "16:9",
    mode: str = "image-to-image", 
    strength: float = 0.15
) -> bytes:
    """
    Generate a variant of an image using Gemini Nano Banana (image-to-image) API.
    
    Args:
        source: Path to the source image file (str) or image bytes (bytes)
        hour: Hour string to insert into the prompt (e.g., "14:30", "matin", etc.)
        format_type: Output format - "thumbnail" (512x288) or "16:9" (1920x1080)
        mode: Mode of the transformation (default: "image-to-image", kept for compatibility)
        strength: Control strength for the transformation (default: 0.15, kept for compatibility)
        
    Returns:
        bytes: PNG image content as bytes in the specified format
        
    Raises:
        ValueError: If required environment variables are missing or invalid format
        FileNotFoundError: If source file doesn't exist (when source is a path)
        Exception: If API request fails
    """
    if not settings.gemini_api_key:
        raise ValueError("Missing Gemini API key. Set GEMINI_API_KEY environment variable.")
    
    if format_type not in FORMAT_DIMENSIONS:
        raise ValueError(f"Invalid format_type. Must be one of: {list(FORMAT_DIMENSIONS.keys())}")
    
    # Construct final prompt by inserting hour into the last sentence
    prompt = PROMPT_BASE.rstrip() + f" : {hour}."
    
    try:
        # Initialize Gemini client
        client = genai.Client(api_key=settings.gemini_api_key)
        
        # Load source image
        if isinstance(source, str):
            if not os.path.exists(source):
                raise FileNotFoundError(f"Source file not found: {source}")
            source_image = Image.open(source)
        else:
            # source is bytes
            source_image = Image.open(io.BytesIO(source))
        
        # Resize image to target format dimensions
        target_width, target_height = FORMAT_DIMENSIONS[format_type]
        # Use LANCZOS resampling for high quality
        source_image = source_image.resize((target_width, target_height), Image.Resampling.LANCZOS)
        
        # Convert PIL Image to bytes for Gemini API
        img_buffer = io.BytesIO()
        source_image.save(img_buffer, format="PNG")
        img_bytes = img_buffer.getvalue()
        
        # Prepare image part for Gemini API
        image_part = types.Part.from_bytes(
            data=img_bytes,
            mime_type="image/png"
        )
        
        # Generate content with Gemini (image-to-image)
        # Using gemini-2.5-flash-image for faster generation
        # Can switch to gemini-3-pro-image for higher quality if needed
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[
                image_part,
                prompt
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"]
            )
        )
        
        # Extract image from response
        # Response structure: response.candidates[0].content.parts
        if not response.candidates or len(response.candidates) == 0:
            raise Exception("No candidates found in API response")
        
        candidate = response.candidates[0]
        if not candidate.content or not candidate.content.parts:
            raise Exception("No content parts found in API response")
        
        for part in candidate.content.parts:
            if hasattr(part, 'text') and part.text is not None:
                logger.info(f"Received text response: {part.text}")
            elif hasattr(part, 'inline_data') and part.inline_data is not None:
                # Get image bytes from inline data
                inline_data = part.inline_data
                
                # Log structure for debugging
                logger.debug(f"inline_data type: {type(inline_data)}, attributes: {[attr for attr in dir(inline_data) if not attr.startswith('_')]}")
                
                # Extract image data - handle different possible structures
                if not hasattr(inline_data, 'data'):
                    raise Exception(f"inline_data missing 'data' attribute. Type: {type(inline_data)}, Attributes: {dir(inline_data)}")
                
                data = inline_data.data
                mime_type = getattr(inline_data, 'mime_type', None)
                logger.debug(f"data type: {type(data)}, mime_type: {mime_type}, length: {len(data) if hasattr(data, '__len__') else 'N/A'}")
                
                # Try to decode as base64 if it's a string, otherwise use as bytes
                if isinstance(data, str):
                    # Decode base64 string to bytes
                    try:
                        image_bytes = base64.b64decode(data)
                        logger.debug(f"Decoded base64 string to {len(image_bytes)} bytes")
                    except Exception as e:
                        logger.error(f"Failed to decode base64 data: {str(e)}")
                        raise Exception(f"Invalid base64 data: {str(e)}")
                elif isinstance(data, bytes):
                    # Data is already bytes
                    image_bytes = data
                    logger.debug(f"Using data as bytes directly: {len(image_bytes)} bytes")
                else:
                    # Try to convert to bytes
                    try:
                        image_bytes = bytes(data)
                        logger.debug(f"Converted data to bytes: {len(image_bytes)} bytes")
                    except Exception as e:
                        raise Exception(f"Cannot convert data to bytes: {str(e)}")
                
                # Verify we have valid image data
                if len(image_bytes) == 0:
                    raise Exception("Image data is empty")
                
                # Check if data looks like a valid image (starts with image magic bytes)
                image_signatures = {
                    b'\x89PNG': 'PNG',
                    b'\xff\xd8\xff': 'JPEG',
                    b'GIF87a': 'GIF',
                    b'GIF89a': 'GIF',
                    b'RIFF': 'WEBP',  # WEBP starts with RIFF
                }
                detected_format = None
                for sig, fmt in image_signatures.items():
                    if image_bytes.startswith(sig):
                        detected_format = fmt
                        break
                
                logger.debug(f"Detected image format: {detected_format or 'Unknown'}, First 10 bytes: {image_bytes[:10]}")
                
                # Convert to PIL Image
                try:
                    img_io = io.BytesIO(image_bytes)
                    img_io.seek(0)  # Reset position to beginning
                    image = Image.open(img_io)
                    image.load()  # Load image data into memory
                    logger.debug(f"Successfully opened image: {image.format}, size: {image.size}")
                except Exception as e:
                    logger.error(f"Failed to open image from bytes. Data length: {len(image_bytes)}, First 20 bytes: {image_bytes[:20].hex()}")
                    raise Exception(f"Cannot identify image format: {str(e)}")
                
                # Ensure output is in the correct format dimensions
                if image.size != (target_width, target_height):
                    image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
                
                img_buffer = io.BytesIO()
                image.save(img_buffer, format="PNG")
                return img_buffer.getvalue()
        
        raise Exception("No image data found in API response")
        
    except Exception as e:
        logger.error(f"Failed to generate variant with Gemini: {str(e)}")
        raise Exception(f"Gemini API error: {str(e)}")

