"""
CSRF protection utilities.

This module provides CSRF token generation, validation, and middleware
to protect against Cross-Site Request Forgery attacks.
"""
import secrets
import logging
from typing import Optional
from fastapi import Request
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# In-memory token storage (in production, consider using Redis or database)
# Format: {token: timestamp}
_csrf_tokens: dict[str, float] = {}

# Token expiration time in seconds (15 minutes)
CSRF_TOKEN_EXPIRATION = 15 * 60


def generate_csrf_token() -> str:
    """Generate a secure random CSRF token."""
    return secrets.token_urlsafe(32)


def store_csrf_token(token: str) -> None:
    """Store a CSRF token with current timestamp."""
    import time
    _csrf_tokens[token] = time.time()
    # Clean up expired tokens periodically
    _cleanup_expired_tokens()


def validate_csrf_token(token: Optional[str]) -> bool:
    """Validate a CSRF token."""
    if not token:
        return False
    
    import time
    current_time = time.time()
    
    # Check if token exists and is not expired
    if token not in _csrf_tokens:
        return False
    
    token_timestamp = _csrf_tokens[token]
    if current_time - token_timestamp > CSRF_TOKEN_EXPIRATION:
        # Token expired, remove it
        _csrf_tokens.pop(token, None)
        return False
    
    return True


def _cleanup_expired_tokens() -> None:
    """Remove expired tokens from storage."""
    import time
    current_time = time.time()
    expired_tokens = [
        token for token, timestamp in _csrf_tokens.items()
        if current_time - timestamp > CSRF_TOKEN_EXPIRATION
    ]
    for token in expired_tokens:
        _csrf_tokens.pop(token, None)


def get_csrf_token_from_request(request: Request) -> Optional[str]:
    """
    Extract CSRF token from request headers.
    
    Note: Token should be sent in the X-CSRF-Token header for all requests,
    including multipart/form-data requests. This avoids the need to read
    the request body in the middleware.
    """
    return request.headers.get("X-CSRF-Token")


def validate_origin(request: Request, allowed_origins: list[str]) -> bool:
    """
    Validate that the request origin matches allowed origins.
    
    Supports wildcard "*" to allow all origins (useful for development).
    """
    # If wildcard is present, accept all origins
    if "*" in allowed_origins:
        return True
    
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    
    # If no origin or referer, we can't validate (should be blocked in production)
    if not origin and not referer:
        return False
    
    # Check origin header
    if origin:
        parsed_origin = urlparse(origin)
        origin_domain = f"{parsed_origin.scheme}://{parsed_origin.netloc}"
        if origin_domain in allowed_origins:
            return True
    
    # Check referer header as fallback
    if referer:
        parsed_referer = urlparse(referer)
        referer_domain = f"{parsed_referer.scheme}://{parsed_referer.netloc}"
        if referer_domain in allowed_origins:
            return True
    
    return False


def check_x_requested_with_header(request: Request) -> bool:
    """Check if X-Requested-With header is present (basic CSRF protection)."""
    return request.headers.get("X-Requested-With") is not None


async def validate_csrf_protection(
    request: Request,
    allowed_origins: list[str],
    require_token: bool = True
) -> bool:
    """
    Validate CSRF protection for a request.
    
    This function implements multiple layers of CSRF protection:
    1. CSRF token validation (if require_token is True)
    2. Origin validation
    3. X-Requested-With header check (as fallback)
    
    Args:
        request: FastAPI request object
        allowed_origins: List of allowed origin domains (supports "*" wildcard)
        require_token: If True, requires a valid CSRF token. If False, uses origin + X-Requested-With
        
    Returns:
        True if request is protected, False otherwise
    """
    # Development mode: wildcard allows all origins, token not required
    # Accept requests even without X-Requested-With (with warning for security awareness)
    if "*" in allowed_origins and not require_token:
        if not check_x_requested_with_header(request):
            logger.debug(
                f"CSRF protection: Development mode - accepting request without X-Requested-With "
                f"for {request.url.path}. Consider adding X-Requested-With header for better security."
            )
        return True
    
    # Validate origin first (unless wildcard is present)
    if "*" not in allowed_origins:
        if not validate_origin(request, allowed_origins):
            logger.warning(f"CSRF protection failed: Invalid origin for {request.url.path}")
            return False
    
    # If token is required, validate it
    if require_token:
        token = get_csrf_token_from_request(request)
        if not validate_csrf_token(token):
            logger.warning(f"CSRF protection failed: Invalid or missing token for {request.url.path}")
            return False
        return True
    
    # Fallback: require X-Requested-With header
    if not check_x_requested_with_header(request):
        logger.warning(f"CSRF protection failed: Missing X-Requested-With header for {request.url.path}")
        return False
    
    return True
