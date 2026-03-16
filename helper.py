"""
Shared helpers used across the TMS chatbot codebase.

Centralises logging setup, config loading, and small utilities
that were previously duplicated in app.py, agent.py, engine.py, and templates.py.
"""

import json
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Dict, List, Optional

import yaml

from config import (
    LOG_BACKUP_COUNT,
    LOG_DIR,
    LOG_FORMAT,
    LOG_MAX_BYTES,
    QUERY_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        RotatingFileHandler(
            os.path.join(LOG_DIR, "app.log"),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
        ),
        logging.StreamHandler(),
    ],
)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``tms_chatbot`` namespace."""
    return logging.getLogger(f"tms_chatbot.{name}" if name else "tms_chatbot")


# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------
def load_yaml_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and return a YAML config file.

    Falls back to the ``AGENT_CONFIG`` env-var, then ``config.yaml``.
    Raises ``FileNotFoundError`` if the resolved path does not exist.
    """
    path = config_path or os.getenv("AGENT_CONFIG", "config.yaml")
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Content normalisation  (LLM response → plain string)
# ---------------------------------------------------------------------------
def normalize_content(content: Any) -> str:
    """Coerce an LLM response (string, list-of-blocks, or other) to a string.

    LangChain tool-calling agents can return content as a list of content
    blocks (e.g. ``[{"type": "text", "text": "..."}]``) instead of a plain
    string.  This helper always returns a string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------
def normalize_sql(sql: str) -> str:
    """Strip trailing whitespace and semicolons from a SQL string."""
    return sql.rstrip().rstrip(";")


# ---------------------------------------------------------------------------
# Thread-with-timeout execution
# ---------------------------------------------------------------------------
def execute_with_timeout(
    func: Callable[[], Any],
    timeout: int = QUERY_TIMEOUT,
    timeout_msg: Optional[str] = None,
    error_prefix: str = "Error executing query",
) -> Any:
    """Run *func()* in a background thread with a timeout.

    Returns the function's result on success, or an error **string** on
    failure / timeout — matching the convention used by ``DatabaseAgent``
    and ``QueryEngine``.
    """
    result: List[Any] = [None]
    error: List[Optional[Exception]] = [None]

    def run() -> None:
        try:
            result[0] = func()
        except Exception as e:
            error[0] = e

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        return timeout_msg or f"Error: Query timed out after {timeout} seconds."
    if error[0]:
        return f"{error_prefix}: {str(error[0])}"
    return result[0]


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------
def sse_event(payload: dict) -> str:
    """Format a dict as a Server-Sent Event data line."""
    return f"data: {json.dumps(payload)}\n\n"
