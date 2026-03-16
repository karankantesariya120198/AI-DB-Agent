"""
All HTTP routes for the TMS chatbot, registered as a Flask Blueprint.
"""

import json
import os
import time
import uuid
from functools import wraps
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import FEEDBACK_TRUNCATE_LENGTH, MAX_MESSAGE_LENGTH, RATE_LIMIT_CHAT, RATE_LIMIT_DEFAULT
from helper import get_logger, normalize_content, sse_event

logger = get_logger("routes")

# ---------------------------------------------------------------------------
# Shared extensions (initialised with the app in create_app)
# ---------------------------------------------------------------------------
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[RATE_LIMIT_DEFAULT],
    storage_uri="memory://",
)

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint("main", __name__)

# ---------------------------------------------------------------------------
# Auth decorator (enabled when API_KEY env var is set)
# ---------------------------------------------------------------------------
_API_KEY = os.getenv("API_KEY")


def require_auth(f: Any) -> Any:
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not _API_KEY:
            return f(*args, **kwargs)
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else request.headers.get("X-API-Key", "")
        if token != _API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route("/")
def index() -> str:
    domain_config = current_app.config["DOMAIN_CONFIG"]
    domain = domain_config.get("domain", {})
    suggested = domain_config.get("suggested_prompts", [
        "Show me all En Route loads",
        "Show driver details",
        "Show customer list",
        "What loads shipped today?",
        "Show equipment summary",
        "Show recent settlements",
    ])
    return render_template(
        "index.html",
        title=domain.get("name", "AI Agent"),
        icon=domain.get("icon", "&#x1F916;"),
        description=domain.get("description", "Ask questions about your data"),
        placeholder=domain.get("placeholder", "Ask a question..."),
        suggested_prompts=json.dumps(suggested),
    )


@bp.route("/api/chat", methods=["POST"])
@require_auth
@limiter.limit(RATE_LIMIT_CHAT)
def chat() -> Response:
    active_requests = current_app.config["ACTIVE_REQUESTS"]
    request_lock = current_app.config["REQUEST_LOCK"]
    agent = current_app.config["AGENT"]

    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400

    message = data.get("message", "")
    if not isinstance(message, str):
        return jsonify({"error": "Message must be a string"}), 400

    message = message.strip()
    session_id = data.get("session_id", str(uuid.uuid4()))

    if not message:
        return jsonify({"error": "Empty message"}), 400

    if len(message) > MAX_MESSAGE_LENGTH:
        return jsonify({"error": f"Message too long (max {MAX_MESSAGE_LENGTH} characters)"}), 400

    request_id = str(uuid.uuid4())
    with request_lock:
        active_requests[request_id] = True

    def generate():
        yield sse_event({"status": "thinking", "request_id": request_id})

        try:
            start = time.time()
            result = agent.query(message, thread_id=session_id)
            duration = time.time() - start

            # Check if cancelled
            with request_lock:
                cancelled = not active_requests.get(request_id, False)
            if cancelled:
                yield sse_event({"error": "Request cancelled"})
                return

            full_response = normalize_content(result["response"])
            sql_queries = result.get("sql_queries", [])
            token_usage = result.get("token_usage", {})
            pagination = result.get("pagination")

            # Stream word-by-word
            words = full_response.split(" ")
            for i, word in enumerate(words):
                with request_lock:
                    cancelled = not active_requests.get(request_id, False)
                if cancelled:
                    yield sse_event({"error": "Request cancelled"})
                    return
                token = word + ("" if i == len(words) - 1 else " ")
                yield sse_event({"word": token})

            # Final event with metadata
            source = result.get("source", "agent")
            yield sse_event({
                "done": True,
                "full_response": full_response,
                "sql_queries": sql_queries,
                "token_usage": token_usage,
                "duration": round(duration, 2),
                "pagination": pagination,
                "source": source,
            })

            logger.info(
                "chat session=%s duration=%.2fs tokens=%s query=%s",
                session_id, duration, json.dumps(token_usage), message[:100],
            )

        except Exception as e:
            logger.error("chat error session=%s error=%s", session_id, str(e))
            yield sse_event({"error": "An internal error occurred. Please try again."})
        finally:
            with request_lock:
                active_requests.pop(request_id, None)

    return Response(
        generate(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@bp.route("/api/cancel", methods=["POST"])
@require_auth
def cancel() -> Response:
    active_requests = current_app.config["ACTIVE_REQUESTS"]
    request_lock = current_app.config["REQUEST_LOCK"]

    data = request.get_json() or {}
    request_id = data.get("request_id")
    with request_lock:
        if request_id and request_id in active_requests:
            active_requests[request_id] = False
            return jsonify({"cancelled": True})
    return jsonify({"cancelled": False})


@bp.route("/api/clear", methods=["POST"])
@require_auth
def clear() -> Response:
    return jsonify({"session_id": str(uuid.uuid4())})


@bp.route("/api/feedback", methods=["POST"])
@require_auth
def feedback() -> Response:
    feedback_store = current_app.config["FEEDBACK_STORE"]
    feedback_lock = current_app.config["FEEDBACK_LOCK"]

    data = request.get_json() or {}
    rating = data.get("rating")
    if rating not in ("up", "down"):
        return jsonify({"error": "Rating must be 'up' or 'down'"}), 400

    entry = {
        "session_id": data.get("session_id", ""),
        "message_index": data.get("message_index", -1),
        "rating": rating,
        "user_message": data.get("user_message", "")[:FEEDBACK_TRUNCATE_LENGTH],
        "assistant_message": data.get("assistant_message", "")[:FEEDBACK_TRUNCATE_LENGTH],
        "timestamp": time.time(),
    }
    with feedback_lock:
        feedback_store.append(entry)
    logger.info("feedback session=%s rating=%s", entry["session_id"], rating)
    return jsonify({"saved": True})


@bp.route("/api/paginate", methods=["POST"])
@require_auth
def paginate() -> Response:
    agent = current_app.config["AGENT"]

    data = request.get_json() or {}
    query_id = data.get("query_id")
    page = data.get("page", 2)

    if not query_id:
        return jsonify({"error": "query_id is required"}), 400

    result = agent.paginate(query_id, page)
    if "error" in result:
        return jsonify(result), 400

    return jsonify(result)


@bp.route("/api/health", methods=["GET"])
def health() -> Response:
    agent = current_app.config["AGENT"]

    checks: dict[str, Any] = {"status": "ok", "timestamp": time.time()}
    try:
        agent.db.run("SELECT 1")
        checks["database"] = "connected"
    except Exception:
        checks["database"] = "unavailable"
        checks["status"] = "degraded"

    checks["llm"] = "configured" if os.getenv("ANTHROPIC_API_KEY") else "missing"
    if checks["llm"] == "missing":
        checks["status"] = "degraded"

    status_code = 200 if checks["status"] == "ok" else 503
    return jsonify(checks), status_code
