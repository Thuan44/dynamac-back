import logging
from datetime import datetime, timedelta
from uuid import UUID
from supabase import create_client, Client
from config import settings
from models.task import TaskCreate, TaskResponse

logger = logging.getLogger(__name__)


class DatabaseService:
    """Service for handling Supabase Postgres database operations."""
    
    def __init__(self):
        self.client: Client = create_client(
            settings.supabase_url,
            settings.supabase_service_key
        )
    
    def create_task(self, task_data: TaskCreate) -> TaskResponse:
        """
        Create a new task in the database.
        
        Args:
            task_data: Task data to insert
            
        Returns:
            Created task with generated ID
            
        Raises:
            Exception: If database operation fails
        """
        try:
            # Set expiration_date if not provided (default: NOW() + 24h)
            if task_data.expiration_date is None:
                task_data.expiration_date = datetime.utcnow() + timedelta(hours=24)
            
            # Insert task into database
            response = self.client.table("tasks").insert({
                "url_source": task_data.url_source,
                "state": task_data.state,
                "expiration_date": task_data.expiration_date.isoformat()
            }).execute()
            
            if not response.data:
                raise Exception("Failed to create task: no data returned")
            
            task_dict = response.data[0]
            task = TaskResponse(
                id=UUID(task_dict["id"]),
                url_source=task_dict["url_source"],
                state=task_dict["state"],
                expiration_date=datetime.fromisoformat(task_dict["expiration_date"].replace("Z", "+00:00")),
                created_at=datetime.fromisoformat(task_dict["created_at"].replace("Z", "+00:00"))
            )
            
            logger.info(f"Task created successfully: {task.id}")
            return task
            
        except Exception as e:
            logger.error(f"Failed to create task: {str(e)}")
            raise
    
    def delete_task(self, task_id: UUID) -> None:
        """
        Delete a task from the database.
        
        Args:
            task_id: ID of the task to delete
        """
        try:
            self.client.table("tasks").delete().eq("id", str(task_id)).execute()
            logger.info(f"Task deleted successfully: {task_id}")
        except Exception as e:
            logger.error(f"Failed to delete task {task_id}: {str(e)}")
            # Don't raise - deletion failure is not critical for rollback


# Global database service instance
database_service = DatabaseService()

