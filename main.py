import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from utils.rate_limiter import limiter
from utils.payload_validation import PayloadSizeLimitMiddleware
from utils.csrf_middleware import CSRFProtectionMiddleware
from utils.security_headers import SecurityHeadersMiddleware
from routes.upload import router as upload_router
from routes.config import router as config_router
from routes.generate import router as generate_router
from routes.status import router as status_router
from routes.csrf import router as csrf_router
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Dynamac Backend API",
    description="Backend API for dynamic macOS wallpaper generation",
    version="1.0.0"
)

# Attach limiter to app state
app.state.limiter = limiter

# Add rate limit exception handler
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Determine allowed origins for CSRF protection
allowed_origins = (
    ["https://dynamac.zite.so"] if os.getenv("ENV") == "production" else ["*"]
)

# Add CSRF protection middleware (before CORS)
# In production, require CSRF tokens. In development, use origin + X-Requested-With
# TEMPORARILY DISABLED - Uncomment to re-enable CSRF protection
# app.add_middleware(
#     CSRFProtectionMiddleware,
#     allowed_origins=allowed_origins,
#     require_token=(os.getenv("ENV") == "production")
# )

# Add payload size limit middleware
app.add_middleware(PayloadSizeLimitMiddleware)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dynamac.zite.so" if os.getenv("ENV") == "production" else "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-Requested-With"],
)

# Add security headers middleware
# Enable HSTS in production (HTTPS required)
is_production = os.getenv("ENV") == "production"
app.add_middleware(
    SecurityHeadersMiddleware,
    enable_hsts=is_production,  # Only enable HSTS in production with HTTPS
    hsts_max_age=31536000,  # 1 year
)

# Include routers
app.include_router(csrf_router)  # CSRF token endpoint (must be before other routers)
app.include_router(upload_router)
app.include_router(config_router)
app.include_router(generate_router)
app.include_router(status_router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Dynamac Backend API", "version": "1.0.0"}


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

