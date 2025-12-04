from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    """Model for creating a new task."""
    url_source: str
    state: str = "uploaded"
    expiration_date: Optional[datetime] = None


class TaskResponse(BaseModel):
    """Model for task response."""
    id: int
    url_source: str
    state: str
    expiration_date: datetime
    created_at: datetime
    
    class Config:
        from_attributes = True


class UploadResponse(BaseModel):
    """Response model for upload endpoint."""
    task_id: int
    url_source: str

