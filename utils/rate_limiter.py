"""
Rate limiter configuration for the application.

This module provides a shared rate limiter instance that can be used
across all routes to enforce rate limiting policies.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Create a shared rate limiter instance
# This will be attached to the FastAPI app state in main.py
limiter = Limiter(key_func=get_remote_address)
