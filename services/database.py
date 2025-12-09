import logging
from datetime import datetime, timedelta
from supabase import create_client, Client
from config import settings
from models.task import TaskCreate, TaskResponse

logger = logging.getLogger(__name__)


def parse_datetime(value):
    """
    Parse datetime from various formats returned by Supabase.
    
    Args:
        value: Can be a string (ISO format), int (Unix timestamp), or datetime object
        
    Returns:
        datetime object
    """
    if isinstance(value, datetime):
        return value
    elif isinstance(value, int):
        # Unix timestamp in seconds
        return datetime.fromtimestamp(value)
    elif isinstance(value, str):
        # ISO format string
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Handle date-only strings (YYYY-MM-DD)
        if len(value) == 10 and value.count("-") == 2:
            return datetime.fromisoformat(value)
        return datetime.fromisoformat(value)
    else:
        raise ValueError(f"Unable to parse datetime from type: {type(value)}")


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
                id=int(task_dict["id"]),
                url_source=task_dict["url_source"],
                state=task_dict["state"],
                expiration_date=parse_datetime(task_dict["expiration_date"]),
                created_at=parse_datetime(task_dict["created_at"])
            )

            logger.info(f"Task created successfully: {task.id}")
            return task
            
        except Exception as e:
            logger.error(f"Failed to create task: {str(e)}")
            raise

    def update_task_config(self, task_id: int, config_payload: dict) -> None:
        """
        Update task configuration and mark it as configured.

        Args:
            task_id: ID of the task to update
            config_payload: Arbitrary JSON-serializable payload to store in config column
        """
        try:
            response = (
                self.client.table("tasks")
                .update(
                    {
                        "config": config_payload,
                        "state": "configured",
                    }
                )
                .eq("id", task_id)
                .execute()
            )

            if response.data is None or len(response.data) == 0:
                raise Exception(f"Task not found for id: {task_id}")

            logger.info(f"Task configuration updated successfully: {task_id}")
        except Exception as e:
            logger.error(f"Failed to update task configuration {task_id}: {str(e)}")
            raise

    def get_task(self, task_id: int) -> TaskResponse:
        """
        Get a task by ID from the database.
        
        Args:
            task_id: ID of the task to retrieve
            
        Returns:
            TaskResponse with task data
            
        Raises:
            Exception: If task not found or database operation fails
        """
        try:
            response = self.client.table("tasks").select("*").eq("id", task_id).execute()
            
            if not response.data or len(response.data) == 0:
                raise Exception(f"Task not found for id: {task_id}")
            
            task_dict = response.data[0]
            
            task = TaskResponse(
                id=int(task_dict["id"]),
                url_source=task_dict["url_source"],
                state=task_dict["state"],
                expiration_date=parse_datetime(task_dict["expiration_date"]),
                created_at=parse_datetime(task_dict["created_at"])
            )
            
            return task
            
        except Exception as e:
            logger.error(f"Failed to get task {task_id}: {str(e)}")
            raise
    
    def delete_task(self, task_id: int) -> None:
        """
        Delete a task from the database.
        
        Args:
            task_id: ID of the task to delete
        """
        try:
            self.client.table("tasks").delete().eq("id", task_id).execute()
            logger.info(f"Task deleted successfully: {task_id}")
        except Exception as e:
            logger.error(f"Failed to delete task {task_id}: {str(e)}")
            # Don't raise - deletion failure is not critical for rollback


# Global database service instance
database_service = DatabaseService()

