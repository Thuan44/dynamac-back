import httpx
import os


# Environment variables
STABLE_DIFFUSION_API_KEY = os.getenv("STABLE_DIFFUSION_API_KEY")
STABILITY_API_HOST = os.getenv("STABILITY_API_HOST")


PROMPT_BASE = """
Transforme cette image en variante strictement conservée. 

Ne modifie aucun élément de la scène, aucun objet, aucune texture, aucune couleur locale, aucune forme, aucun angle. 

Reproduis l'image de manière identique. 

Seul l'éclairage doit changer. 

Ajuste exclusivement :

- la luminosité globale

- l'exposition au soleil

- l'intensité lumineuse directionnelle

- la balance entre lumière directe et diffuse

Aucune autre modification. 

Pas d'ajouts, pas de suppressions, pas de stylisation. 

Pas d'altération de netteté ou profondeur. 

Respect absolu de la composition originale à l'exception du ciel s'il est visible et de nuages s'il y en a. 

Applique l'ajustement lumineux en te basant sur l'heure d'affichage fournie
"""


def generate_variant(source_path: str, hour: str, strength: float = 0.15) -> bytes:
    """
    Generate a variant of an image using Stable Diffusion image-to-image API.
    
    Args:
        source_path: Path to the source image file
        hour: Hour string to insert into the prompt (e.g., "14:30", "matin", etc.)
        strength: Control strength for the transformation (default: 0.15)
        
    Returns:
        bytes: PNG image content as bytes
        
    Raises:
        ValueError: If required environment variables are missing
        FileNotFoundError: If source file doesn't exist
        httpx.HTTPStatusError: If API request fails
    """
    if not STABLE_DIFFUSION_API_KEY:
        raise ValueError("STABLE_DIFFUSION_API_KEY environment variable is not set")
    
    if not STABILITY_API_HOST:
        raise ValueError("STABILITY_API_HOST environment variable is not set")
    
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Source file not found: {source_path}")
    
    # Construct final prompt by inserting hour into the last sentence
    prompt = PROMPT_BASE.rstrip() + f" : {hour}."
    
    # Open source file in binary mode
    with open(source_path, "rb") as image_file:
        # Prepare multipart form data
        files = {
            "image": image_file
        }
        
        data = {
            "prompt": prompt,
            "strength": str(strength),
            "seed": "0",
            "mode": "preserve",
            "output_format": "png"
        }
        
        headers = {
            "Authorization": f"Bearer {STABLE_DIFFUSION_API_KEY}"
        }
        
        # Make POST request to Stable Diffusion API
        with httpx.Client() as client:
            response = client.post(
                f"{STABILITY_API_HOST}/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
                files=files,
                data=data,
                headers=headers,
                timeout=60.0
            )
            
            # Strict error handling
            response.raise_for_status()
            
            # Return PNG content as bytes
            return response.content

