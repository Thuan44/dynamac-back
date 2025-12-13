"""
CSRF token endpoint.

This endpoint allows clients to obtain a CSRF token for protecting state-changing requests.
"""
import logging
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from utils.csrf import generate_csrf_token, store_csrf_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["csrf"])


@router.get("/csrf-token")
async def get_csrf_token(response: Response) -> JSONResponse:
    """
    Get a CSRF token for protecting state-changing requests.
    
    The token should be included in subsequent POST/PUT/DELETE/PATCH requests
    either as:
    - Header: X-CSRF-Token: <token>
    - Form field: csrf_token: <token>
    
    Returns:
        JSON response with CSRF token
    """
    token = generate_csrf_token()
    store_csrf_token(token)
    
    logger.debug("CSRF token generated and stored")
    
    # Return token in JSON response
    # Client should store this token and include it in subsequent requests
    return JSONResponse(
        content={"csrf_token": token},
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        }
    )
