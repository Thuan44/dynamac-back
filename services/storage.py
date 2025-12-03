import logging
from supabase import create_client, Client
from config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """Service for handling Supabase Storage operations."""
    
    def __init__(self):
        self.client: Client = create_client(
            settings.supabase_url,
            settings.supabase_service_key
        )
        self.bucket = settings.storage_bucket
    
    def upload_file(self, file_content: bytes, file_name: str, content_type: str) -> str:
        """
        Upload a file to Supabase Storage.
        
        Args:
            file_content: Binary content of the file
            file_name: Name of the file
            content_type: MIME type of the file
            
        Returns:
            Public URL of the uploaded file
            
        Raises:
            Exception: If upload fails
        """
        try:
            # Upload file to storage
            response = self.client.storage.from_(self.bucket).upload(
                path=file_name,
                file=file_content,
                file_options={"content-type": content_type}
            )
            
            # Get public URL
            public_url = self.client.storage.from_(self.bucket).get_public_url(file_name)
            
            logger.info(f"File uploaded successfully: {file_name}")
            return public_url
            
        except Exception as e:
            logger.error(f"Failed to upload file {file_name}: {str(e)}")
            raise
    
    def delete_file(self, file_name: str) -> None:
        """
        Delete a file from Supabase Storage.
        
        Args:
            file_name: Name of the file to delete
        """
        try:
            self.client.storage.from_(self.bucket).remove([file_name])
            logger.info(f"File deleted successfully: {file_name}")
        except Exception as e:
            logger.error(f"Failed to delete file {file_name}: {str(e)}")
            # Don't raise - deletion failure is not critical for rollback


# Global storage service instance
storage_service = StorageService()

