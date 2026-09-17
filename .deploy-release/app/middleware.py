"""
Middleware for logging, error handling, and rate limiting.
"""

import time
import json
import logging
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests with timing and response status."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        try:
            # Log incoming request
            user_id = request.headers.get("X-User-ID", "anonymous")
            logger.info(f"[REQUEST] {request.method} {request.url.path} | User: {user_id}")

            response = await call_next(request)

            # Log response
            process_time = time.time() - start_time
            logger.info(
                f"[RESPONSE] {request.method} {request.url.path} | "
                f"Status: {response.status_code} | Duration: {process_time:.2f}s"
            )

            return response

        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                f"[ERROR] {request.method} {request.url.path} | "
                f"Exception: {str(e)} | Duration: {process_time:.2f}s"
            )
            raise


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory rate limiting per user.
    Limits: 100 requests per minute per user.
    """

    def __init__(self, app):
        super().__init__(app)
        self.requests = defaultdict(list)
        self.limit_per_minute = 100

    async def dispatch(self, request: Request, call_next):
        user_id = request.headers.get("X-User-ID", "anonymous")
        now = datetime.utcnow()
        minute_ago = now - timedelta(minutes=1)

        # Clean old requests
        self.requests[user_id] = [
            req_time for req_time in self.requests[user_id]
            if req_time > minute_ago
        ]

        # Check limit
        if len(self.requests[user_id]) >= self.limit_per_minute:
            logger.warning(f"[RATE_LIMIT] User {user_id} exceeded limit")
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded: {self.limit_per_minute} requests per minute"}
            )

        # Track this request
        self.requests[user_id].append(now)

        response = await call_next(request)
        return response


class InputValidationMiddleware(BaseHTTPMiddleware):
    """Validate common inputs and reject obviously bad requests early."""

    async def dispatch(self, request: Request, call_next):
        # Check workspace_id if provided
        if "workspace_id" in request.query_params:
            workspace_id = request.query_params["workspace_id"]
            if not workspace_id or len(workspace_id) < 3:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid workspace_id"}
                )

        # Check request body size (max 10MB)
        if request.headers.get("content-length"):
            try:
                content_length = int(request.headers["content-length"])
                if content_length > 10 * 1024 * 1024:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large (max 10MB)"}
                    )
            except ValueError:
                pass

        response = await call_next(request)
        return response


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Catch unhandled exceptions and return proper error responses."""

    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            logger.error(f"[UNHANDLED ERROR] {str(e)}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error",
                    "error_id": str(id(e))  # For debugging
                }
            )
