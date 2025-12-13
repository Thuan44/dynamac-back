"""
CSRF protection middleware for FastAPI.

This middleware validates CSRF protection for all state-changing HTTP methods
(POST, PUT, DELETE, PATCH) according to the security audit recommendations.
"""
import logging
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from utils.csrf import validate_csrf_protection

logger = logging.getLogger(__name__)

# HTTP methods that require CSRF protection
PROTECTED_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Endpoints that should be excluded from CSRF protection
EXCLUDED_PATHS = {
    "/health",
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/csrf-token",  # Endpoint to get CSRF token
}


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    """Middleware to protect against CSRF attacks."""
    
    def __init__(
        self,
        app: ASGIApp,
        allowed_origins: list[str],
        require_token: bool = True
    ):
        """
        Initialize CSRF protection middleware.
        
        Args:
            app: ASGI application
            allowed_origins: List of allowed origin domains
            require_token: If True, requires CSRF token. If False, uses origin + X-Requested-With
        """
        super().__init__(app)
        self.allowed_origins = allowed_origins
        self.require_token = require_token
    
    async def dispatch(self, request: Request, call_next):
        """Process request and validate CSRF protection for state-changing methods."""
        # Skip CSRF protection for excluded paths
        if request.url.path in EXCLUDED_PATHS:
            return await call_next(request)
        
        # Only protect state-changing HTTP methods
        if request.method in PROTECTED_METHODS:
            try:
                # Validate CSRF protection
                is_valid = await validate_csrf_protection(
                    request=request,
                    allowed_origins=self.allowed_origins,
                    require_token=self.require_token
                )
                
                if not is_valid:
                    logger.warning(
                        f"CSRF protection failed for {request.method} {request.url.path} "
                        f"from {request.client.host if request.client else 'unknown'}"
                    )
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={
                            "detail": "CSRF protection failed. Invalid or missing CSRF token, "
                                     "or request origin does not match allowed origins."
                        }
                    )
            except Exception as e:
                logger.error(f"Error in CSRF protection middleware: {str(e)}", exc_info=True)
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={"detail": "Internal server error during CSRF validation"}
                )
        
        # Process request
        response = await call_next(request)
        
        return response
