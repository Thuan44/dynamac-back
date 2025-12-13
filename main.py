import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes.upload import router as upload_router
from routes.config import router as config_router
from routes.generate import router as generate_router
from routes.status import router as status_router
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

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dynamac.zite.so" if os.getenv("ENV") == "production" else "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

# Include routers
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

