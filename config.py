"""
Centralised constants and configuration defaults for the TMS chatbot.

All tunable values live here so they can be adjusted in one place.
"""

import os

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per log file
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# ---------------------------------------------------------------------------
# Flask / HTTP
# ---------------------------------------------------------------------------
APP_PORT = 5000
MAX_MESSAGE_LENGTH = 2000
FEEDBACK_TRUNCATE_LENGTH = 500
RATE_LIMIT_DEFAULT = "60 per minute"
RATE_LIMIT_CHAT = "30 per minute"

# ---------------------------------------------------------------------------
# Database defaults (used as fallbacks when env vars are not set)
# ---------------------------------------------------------------------------
DEFAULT_DB_TYPE = "sqlite"
DEFAULT_DB_PATH = "database.db"
DEFAULT_DB_HOST = "localhost"
DEFAULT_MYSQL_PORT = "3306"
DEFAULT_POSTGRESQL_PORT = "5432"

# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------
QUERY_TIMEOUT = 30          # seconds
PAGE_SIZE = 10              # rows per page
CACHE_TTL = 300             # seconds (5 minutes)
MAX_QUERY_RETRIES = 2

# ---------------------------------------------------------------------------
# LLM models
# ---------------------------------------------------------------------------
DEFAULT_AGENT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_AGENT_TEMPERATURE = 0.1
DEFAULT_AGENT_MAX_TOKENS = 4096

CLASSIFY_MODEL = "claude-haiku-4-5-20251001"
CLASSIFY_MAX_TOKENS = 300

FORMAT_MODEL = "claude-haiku-4-5-20251001"
FORMAT_MAX_TOKENS = 500

# ---------------------------------------------------------------------------
# Query engine / sessions
# ---------------------------------------------------------------------------
SESSION_TTL = 1800              # seconds (30 minutes)
CONFIDENCE_THRESHOLD = 0.6
CONTEXT_CLEANUP_INTERVAL = 100  # cleanup every N queries

ENTITY_KEYS = frozenset({
    "customer_name", "driver_name", "load_id", "equipment_name",
    "trip_id", "ponum", "location", "status",
})
