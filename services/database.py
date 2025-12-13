import logging
from datetime import datetime, timedelta
from typing import Optional
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
                created_at=parse_datetime(task_dict["created_at"]),
                config=task_dict["config"]
            )
            
            return task
            
        except Exception as e:
            logger.error(f"Failed to get task {task_id}: {str(e)}")
            raise
    
    def get_task_raw(self, task_id: int) -> dict:
        """
        Get raw task data by ID from the database, including all fields.
        
        Args:
            task_id: ID of the task to retrieve
            
        Returns:
            Dictionary with all task data including urls_previews
            
        Raises:
            Exception: If task not found or database operation fails
        """
        try:
            response = self.client.table("tasks").select("*").eq("id", task_id).execute()
            
            if not response.data or len(response.data) == 0:
                raise Exception(f"Task not found for id: {task_id}")
            
            return response.data[0]
            
        except Exception as e:
            logger.error(f"Failed to get raw task {task_id}: {str(e)}")
            raise
    
    def update_task_state(self, task_id: int, new_state: str) -> None:
        """
        Update the state of a task.
        
        Args:
            task_id: ID of the task to update
            new_state: New state value
        """
        try:
            response = (
                self.client.table("tasks")
                .update({"state": new_state})
                .eq("id", task_id)
                .execute()
            )
            
            if response.data is None or len(response.data) == 0:
                raise Exception(f"Task not found for id: {task_id}")
            
            logger.info(f"Task state updated successfully: {task_id} -> {new_state}")
        except Exception as e:
            logger.error(f"Failed to update task state {task_id}: {str(e)}")
            raise
    
    def update_task_previews(self, task_id: int, previews: list, new_state: str) -> None:
        """
        Update task with preview URLs and state.
        
        Args:
            task_id: ID of the task to update
            previews: List of preview objects with "hour" and "url" keys
            new_state: New state value (typically "previews_ready")
        """
        try:
            response = (
                self.client.table("tasks")
                .update({
                    "urls_previews": {"variants": previews},
                    "state": new_state
                })
                .eq("id", task_id)
                .execute()
            )
            
            if response.data is None or len(response.data) == 0:
                raise Exception(f"Task not found for id: {task_id}")
            
            logger.info(f"Task previews updated successfully: {task_id} with {len(previews)} previews")
        except Exception as e:
            logger.error(f"Failed to update task previews {task_id}: {str(e)}")
            raise

    def update_task_renders(self, task_id: int, renders: list, new_state: str) -> None:
        """
        Update task with upscaled render URLs and state.
        
        Args:
            task_id: ID of the task to update
            renders: List of render objects with "hour" and "url" keys
            new_state: New state value (typically "renders_ready")
        """
        try:
            response = (
                self.client.table("tasks")
                .update({
                    "urls_renders": {"variants": renders},
                    "state": new_state
                })
                .eq("id", task_id)
                .execute()
            )
            
            if response.data is None or len(response.data) == 0:
                raise Exception(f"Task not found for id: {task_id}")
            
            logger.info(f"Task renders updated successfully: {task_id} with {len(renders)} renders")
        except Exception as e:
            logger.error(f"Failed to update task renders {task_id}: {str(e)}")
            raise
    
    def update_task_final_url(self, task_id: int, url_finale: str) -> None:
        """
        Update task with final HEIC file URL.
        
        Args:
            task_id: ID of the task to update
            url_finale: URL of the final HEIC file
        """
        try:
            response = (
                self.client.table("tasks")
                .update({"url_finale": url_finale})
                .eq("id", task_id)
                .execute()
            )
            
            if response.data is None or len(response.data) == 0:
                raise Exception(f"Task not found for id: {task_id}")
            
            logger.info(f"Task final URL updated successfully: {task_id} -> {url_finale}")
        except Exception as e:
            logger.error(f"Failed to update task final URL {task_id}: {str(e)}")
            raise
    
    def update_task_processing_started(self, task_id: int) -> datetime:
        """
        Mark task as processing and store started_at timestamp.
        
        Args:
            task_id: ID of the task to update
            
        Returns:
            The started_at datetime that was stored
        """
        try:
            started_at = datetime.utcnow()
            response = (
                self.client.table("tasks")
                .update({
                    "state": "processing",
                    "started_at": started_at.isoformat()
                })
                .eq("id", task_id)
                .execute()
            )
            
            if response.data is None or len(response.data) == 0:
                raise Exception(f"Task not found for id: {task_id}")
            
            logger.info(f"Task marked as processing with started_at: {task_id}")
            return started_at
        except Exception as e:
            logger.error(f"Failed to update task processing started {task_id}: {str(e)}")
            raise
    
    def get_task_started_at(self, task_id: int) -> Optional[datetime]:
        """
        Get the started_at timestamp for a task.
        
        Args:
            task_id: ID of the task to retrieve
            
        Returns:
            The started_at datetime if available, None otherwise
        """
        try:
            task_data = self.get_task_raw(task_id)
            started_at = task_data.get("started_at")
            if started_at:
                return parse_datetime(started_at)
            return None
        except Exception as e:
            logger.error(f"Failed to get task started_at {task_id}: {str(e)}")
            return None
    
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

