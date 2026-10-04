"""Utility functions."""

import logging
import os
import traceback
from datetime import date, datetime
from decimal import Decimal

DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")

# Suppress noisy debug logs from dependencies
# Must be done before uvicorn configures logging
logging.getLogger("python_multipart").setLevel(logging.WARNING)
logging.getLogger("python_multipart.multipart").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


def log_exception(e: Exception, context: str = "") -> None:
    """Log exception with traceback when DEBUG is enabled."""
    if DEBUG:
        logger.error(f"{context}: {e}\n{traceback.format_exc()}")

