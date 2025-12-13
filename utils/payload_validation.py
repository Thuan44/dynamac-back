import logging
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from config import settings

logger = logging.getLogger(__name__)


class PayloadSizeLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to limit the size of JSON payloads in requests."""

    async def dispatch(self, request: Request, call_next):
        # Only check POST, PUT, PATCH requests that might have JSON payloads
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            
            if content_length:
                try:
                    size = int(content_length)
                    if size > settings.max_json_payload_size:
                        client_host = request.client.host if request.client else "unknown"
                        logger.warning(
                            f"Request payload too large: {size} bytes "
                            f"(max: {settings.max_json_payload_size} bytes) from {client_host}"
                        )
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"Payload size exceeds maximum allowed size of {settings.max_json_payload_size / (1024 * 1024):.1f}MB"
                        )
                except HTTPException:
                    raise
                except (ValueError, TypeError):
                    # Invalid content-length header, let it pass and be handled by FastAPI
                    pass
        
        response = await call_next(request)
        return response
