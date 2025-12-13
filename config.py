from urllib.parse import urlparse
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables.
    
    All secrets are validated at startup to ensure they are present and valid.
    Secrets are never logged or exposed in error messages.
    """
    
    # Environment
    env: str = "development"

    supabase_url: str
    supabase_service_key: str

    gemini_api_key: str

    # Storage configuration
    storage_bucket: str = "images"
    
    # Upload limits
    max_file_size: int = 20 * 1024 * 1024  # 20MB in bytes
    allowed_image_types: list[str] = ["image/jpeg", "image/jpg", "image/png", "image/webp"]
    
    # URL validation
    allowed_url_domains: list[str] = []  # Empty list means auto-detect from SUPABASE_URL
    max_download_size: int = 50 * 1024 * 1024  # 50MB max download size
    
    # JSON payload limits
    max_json_payload_size: int = 10 * 1024 * 1024  # 10MB max JSON payload size
    max_variants_count: int = 3  # Maximum number of variants (configurable via MAX_VARIANTS_COUNT env var)
    
    # Timeout configurations (in seconds)
    timeout_http_request: float = 30.0  # HTTP request timeout
    timeout_http_connect: float = 10.0  # HTTP connection timeout
    timeout_gemini_api: float = 120.0  # Gemini API call timeout (2 minutes)
    timeout_image_processing: float = 60.0  # Image processing operations timeout (1 minute)
    timeout_storage_operation: float = 60.0  # Storage upload/download timeout (1 minute)
    timeout_heic_creation: float = 240.0  # HEIC file creation timeout (4 minutes)
    timeout_background_task: float = 600.0  # Global timeout for background tasks (10 minutes)
    
    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, v: str) -> str:
        """Validate that SUPABASE_URL is a valid HTTPS URL."""
        if not v:
            raise ValueError("SUPABASE_URL is required and cannot be empty")
        if not v.startswith("https://"):
            raise ValueError("SUPABASE_URL must be a valid HTTPS URL (must start with https://)")
        if len(v) < 15:  # Minimum reasonable URL length
            raise ValueError("SUPABASE_URL appears to be invalid (too short)")
        return v
    
    @field_validator("supabase_service_key")
    @classmethod
    def validate_service_key(cls, v: str) -> str:
        """Validate that SUPABASE_SERVICE_KEY is present and has minimum length."""
        if not v:
            raise ValueError("SUPABASE_SERVICE_KEY is required and cannot be empty")
        if len(v) < 20:
            raise ValueError("SUPABASE_SERVICE_KEY must be at least 20 characters long")
        return v
    
    @field_validator("gemini_api_key")
    @classmethod
    def validate_gemini_key(cls, v: str) -> str:
        """Validate that GEMINI_API_KEY is present and has minimum length."""
        if not v:
            raise ValueError("GEMINI_API_KEY is required and cannot be empty")
        if len(v) < 20:
            raise ValueError("GEMINI_API_KEY must be at least 20 characters long")
        return v
    
    def __repr__(self) -> str:
        """Custom repr that never exposes secrets."""
        return (
            f"Settings("
            f"env={self.env!r}, "
            f"supabase_url={self._mask_url(self.supabase_url)!r}, "
            f"storage_bucket={self.storage_bucket!r}, "
            f"max_file_size={self.max_file_size}, "
            f"max_variants_count={self.max_variants_count}"
            f")"
        )
    
    @staticmethod
    def _mask_url(url: str) -> str:
        """Mask sensitive parts of URL for logging."""
        if not url:
            return ""
        # Keep only the domain part, mask the rest
        try:
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}/***"
        except Exception:
            return "***"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
# This will raise ValidationError at import time if secrets are missing or invalid
settings = Settings()

