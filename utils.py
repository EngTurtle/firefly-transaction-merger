"""Utility functions and decorators."""

import functools
import logging
import os
import traceback
from typing import Callable

DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")

# Suppress noisy debug logs from dependencies
# Must be done before uvicorn configures logging
logging.getLogger("python_multipart").setLevel(logging.WARNING)
logging.getLogger("python_multipart.multipart").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def log_exception(e: Exception, context: str = "") -> None:
    """Log exception with traceback when DEBUG is enabled."""
    if DEBUG:
        logger.error(f"{context}: {e}\n{traceback.format_exc()}")


def handle_errors(
    templates,
    error_template: str,
    extra_context: Callable[..., dict] | None = None,
):
    """Decorator to catch exceptions, log them in DEBUG mode, and render error template.

    Args:
        templates: Jinja2Templates instance
        error_template: Template to render on error
        extra_context: Optional callable to add extra context from kwargs
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if DEBUG:
                    logger.error(f"{func.__name__}: {e}\n{traceback.format_exc()}")
                request = kwargs.get("request") or args[0]
                context = {"request": request, "error": str(e)}
                if extra_context:
                    context.update(extra_context(**kwargs))
                return templates.TemplateResponse(
                    error_template, context, status_code=500
                )

        return wrapper

    return decorator
