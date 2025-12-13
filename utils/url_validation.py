"""
URL validation utilities for secure image downloads.

This module provides validation functions to prevent SSRF attacks by:
- Validating URLs point to allowed domains (whitelist)
- Blocking localhost and private IP addresses
- Ensuring only HTTP/HTTPS schemes are used
- Validating Supabase storage URL format
"""

import logging
import ipaddress
from urllib.parse import urlparse
from typing import Optional
from fastapi import HTTPException, status
from config import settings

logger = logging.getLogger(__name__)


def is_private_ip(hostname: str) -> bool:
    """
    Check if a hostname resolves to a private IP address.
    
    Args:
        hostname: Hostname or IP address to check
        
    Returns:
        True if the hostname is a private IP, False otherwise
    """
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        # Not an IP address, might be a hostname
        # Check for common localhost hostnames
        localhost_hostnames = [
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "::1",
            "local",
        ]
        return hostname.lower() in localhost_hostnames


def is_allowed_domain(hostname: str) -> bool:
    """
    Check if a hostname belongs to an allowed domain.
    
    Args:
        hostname: Hostname to check
        
    Returns:
        True if the hostname is in the allowed domains list, False otherwise
    """
    if not hostname:
        return False
    
    # Get allowed domains from settings
    allowed_domains = getattr(settings, "allowed_url_domains", [])
    
    # If no allowed domains configured, extract from SUPABASE_URL
    if not allowed_domains:
        try:
            supabase_parsed = urlparse(settings.supabase_url)
            supabase_domain = supabase_parsed.hostname
            if supabase_domain:
                # Extract base domain (e.g., "supabase.co" from "project.supabase.co")
                parts = supabase_domain.split(".")
                if len(parts) >= 2:
                    base_domain = ".".join(parts[-2:])  # Get last two parts
                    allowed_domains = [base_domain, supabase_domain]
        except Exception as e:
            logger.warning(f"Failed to extract domain from SUPABASE_URL: {str(e)}")
            # Fallback to default Supabase domains
            allowed_domains = ["supabase.co"]
    
    # Check if hostname matches any allowed domain
    hostname_lower = hostname.lower()
    for domain in allowed_domains:
        domain_lower = domain.lower()
        # Exact match or subdomain match
        if hostname_lower == domain_lower or hostname_lower.endswith(f".{domain_lower}"):
            return True
    
    return False


def validate_url(url: str, max_size: Optional[int] = None) -> None:
    """
    Validate that a URL is safe to download from.
    
    This function performs security checks to prevent SSRF attacks:
    1. Validates URL scheme (only http/https)
    2. Blocks private IP addresses and localhost
    3. Validates domain is in whitelist (Supabase domains)
    
    Args:
        url: URL to validate
        max_size: Optional maximum response size in bytes (not enforced here, 
                  but can be used by caller)
        
    Raises:
        HTTPException: If URL is invalid or not allowed
    """
    if not url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL cannot be empty"
        )
    
    try:
        parsed = urlparse(url)
    except Exception as e:
        logger.warning(f"Invalid URL format: {url}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL format"
        )
    
    # Only allow HTTP and HTTPS schemes
    if parsed.scheme not in ["http", "https"]:
        logger.warning(f"Invalid URL scheme: {parsed.scheme} for URL: {url}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only HTTP and HTTPS URLs are allowed"
        )
    
    # Require HTTPS for security (recommended)
    if parsed.scheme != "https":
        logger.warning(f"Non-HTTPS URL detected: {url}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only HTTPS URLs are allowed for security"
        )
    
    hostname = parsed.hostname
    if not hostname:
        logger.warning(f"Missing hostname in URL: {url}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL: missing hostname"
        )
    
    # Block private IPs and localhost
    if is_private_ip(hostname):
        logger.warning(f"Blocked private IP/localhost URL: {url}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URLs pointing to localhost or private networks are not allowed"
        )
    
    # Validate domain is in whitelist
    if not is_allowed_domain(hostname):
        logger.warning(f"Blocked non-whitelisted domain: {hostname} in URL: {url}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL domain is not in the allowed list"
        )
    
    logger.debug(f"URL validation passed: {url}")


def validate_supabase_storage_url(url: str) -> None:
    """
    Validate that a URL is a valid Supabase Storage public URL.
    
    Expected format: https://[project].supabase.co/storage/v1/object/public/[bucket]/[path]
    
    Args:
        url: URL to validate
        
    Raises:
        HTTPException: If URL is not a valid Supabase Storage URL
    """
    # First perform general URL validation
    validate_url(url)
    
    try:
        parsed = urlparse(url)
        
        # Check if path matches Supabase Storage pattern
        if not parsed.path.startswith("/storage/v1/object/public/"):
            logger.warning(f"URL does not match Supabase Storage pattern: {url}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL does not match Supabase Storage format"
            )
        
        # Extract bucket and path
        path_parts = parsed.path.replace("/storage/v1/object/public/", "").split("/", 1)
        if len(path_parts) < 1:
            logger.warning(f"Invalid Supabase Storage URL path: {url}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Supabase Storage URL format"
            )
        
        logger.debug(f"Supabase Storage URL validated: bucket={path_parts[0]}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating Supabase Storage URL: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to validate Supabase Storage URL"
        )
