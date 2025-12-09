import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    supabase_url: str
    supabase_service_key: str

    gemini_api_key: str

    # Storage configuration
    storage_bucket: str = "images"
    
    # Upload limits
    max_file_size: int = 20 * 1024 * 1024  # 20MB in bytes
    allowed_image_types: list[str] = ["image/jpeg", "image/jpg", "image/png", "image/webp"]
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()

