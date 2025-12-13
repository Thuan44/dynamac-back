"""
HTTP Security Headers middleware for FastAPI.

This middleware adds security headers to all HTTP responses according to
OWASP best practices and the security audit recommendations.
"""
import logging
import os
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all HTTP responses."""
    
    def __init__(
        self,
        app: ASGIApp,
        enable_hsts: bool = None,
        hsts_max_age: int = 31536000,  # 1 year in seconds
        csp_policy: str = None
    ):
        """
        Initialize security headers middleware.
        
        Args:
            app: ASGI application
            enable_hsts: If True, enables HSTS header. If None, auto-detects from request scheme.
            hsts_max_age: HSTS max-age value in seconds (default: 1 year)
            csp_policy: Content-Security-Policy string. If None, uses a restrictive default.
        """
        super().__init__(app)
        self.enable_hsts = enable_hsts
        self.hsts_max_age = hsts_max_age
        
        # Default CSP policy - restrictive for API endpoints
        # Allows same-origin resources and blocks inline scripts/styles
        self.csp_policy = csp_policy or (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
    
    async def dispatch(self, request: Request, call_next):
        """Process request and add security headers to response."""
        response = await call_next(request)
        
        # X-Content-Type-Options: Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # X-Frame-Options: Prevent clickjacking attacks
        response.headers["X-Frame-Options"] = "DENY"
        
        # X-XSS-Protection: Enable XSS filtering (legacy but still useful for older browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # Strict-Transport-Security: Force HTTPS connections
        # Only add if HTTPS is detected or explicitly enabled
        should_add_hsts = self.enable_hsts
        if should_add_hsts is None:
            # Auto-detect: add HSTS if request is over HTTPS
            should_add_hsts = request.url.scheme == "https"
        
        if should_add_hsts:
            # Include subdomains and preload for better security
            response.headers["Strict-Transport-Security"] = (
                f"max-age={self.hsts_max_age}; includeSubDomains; preload"
            )
        
        # Content-Security-Policy: Control resource loading
        response.headers["Content-Security-Policy"] = self.csp_policy
        
        # Referrer-Policy: Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Permissions-Policy: Control browser features (formerly Feature-Policy)
        response.headers["Permissions-Policy"] = (
            "geolocation=(), "
            "microphone=(), "
            "camera=(), "
            "payment=(), "
            "usb=(), "
            "magnetometer=(), "
            "gyroscope=(), "
            "speaker=()"
        )
        
        return response
