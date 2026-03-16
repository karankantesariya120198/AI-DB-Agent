import os
import json
import uuid
import time
import logging
from logging.handlers import RotatingFileHandler
from functools import wraps

import yaml
from flask import Flask, request, Response, render_template, jsonify, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from agent import create_agent_instance

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(
            os.path.join(LOG_DIR, "app.log"),
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("tms_chatbot")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)

# CORS — configurable origins via env var
CORS(app, origins=os.getenv("CORS_ORIGINS", "*").split(","))

# Rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["60 per minute"],
    storage_uri="memory://",
)

# Load domain config for UI values
config_path = os.path.join("domains", os.getenv("AGENT_CONFIG", "config.yaml"))
with open(config_path, "r") as f:
    app_config = yaml.safe_load(f)

# Module-level agent singleton
agent = create_agent_instance(config_path)

# In-memory feedback store (swap for DB in production)
feedback_store = []

# Active request tracking for cancellation
active_requests = {}

# ---------------------------------------------------------------------------
# Auth middleware (enabled when API_KEY is set in .env)
# ---------------------------------------------------------------------------
API_KEY = os.getenv("API_KEY")


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not API_KEY:
            return f(*args, **kwargs)
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else request.headers.get("X-API-Key", "")
        if token != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Request timing & logging
# ---------------------------------------------------------------------------
@app.before_request
def before_request_hook():
    g.start_time = time.time()


@app.after_request
def after_request_hook(response):
    if hasattr(g, "start_time"):
        duration = time.time() - g.start_time
        logger.info(
            "request=%s %s status=%s duration=%.3fs",
            request.method, request.path, response.status_code, duration,
        )
    return response


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_MESSAGE_LENGTH = 2000


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    domain = app_config.get("domain", {})
    suggested = app_config.get("suggested_prompts", [
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


@app.route("/api/chat", methods=["POST"])
@require_auth
@limiter.limit("30 per minute")
def chat():
    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400

    message = data.get("message", "")
    if not isinstance(message, str):
        return jsonify({"error": "Message must be a string"}), 400

    message = message.strip()
    session_id = data.get("session_id", str(uuid.uuid4()))

    if not message:
        return Response(
            "data: " + json.dumps({"error": "Empty message"}) + "\n\n",
            content_type="text/event-stream",
        )

    if len(message) > MAX_MESSAGE_LENGTH:
        return Response(
            "data: " + json.dumps({
                "error": f"Message too long (max {MAX_MESSAGE_LENGTH} characters)"
            }) + "\n\n",
            content_type="text/event-stream",
        )

    request_id = str(uuid.uuid4())
    active_requests[request_id] = True

    def generate():
        yield "data: " + json.dumps({"status": "thinking", "request_id": request_id}) + "\n\n"

        try:
            start = time.time()
            result = agent.query(message, thread_id=session_id)
            duration = time.time() - start

            # Check if cancelled
            if not active_requests.get(request_id, False):
                yield "data: " + json.dumps({"error": "Request cancelled"}) + "\n\n"
                return

            full_response = result["response"]
            # Safety: ensure full_response is always a string
            if not isinstance(full_response, str):
                if isinstance(full_response, list):
                    full_response = "\n".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in full_response
                    )
                else:
                    full_response = str(full_response)
            sql_queries = result.get("sql_queries", [])
            token_usage = result.get("token_usage", {})
            pagination = result.get("pagination")

            # Stream word-by-word
            words = full_response.split(" ")
            for i, word in enumerate(words):
                if not active_requests.get(request_id, False):
                    yield "data: " + json.dumps({"error": "Request cancelled"}) + "\n\n"
                    return
                token = word + ("" if i == len(words) - 1 else " ")
                yield "data: " + json.dumps({"word": token}) + "\n\n"

            # Final event with metadata
            source = result.get("source", "agent")
            yield "data: " + json.dumps({
                "done": True,
                "full_response": full_response,
                "sql_queries": sql_queries,
                "token_usage": token_usage,
                "duration": round(duration, 2),
                "pagination": pagination,
                "source": source,
            }) + "\n\n"

            logger.info(
                "chat session=%s duration=%.2fs tokens=%s query=%s",
                session_id, duration, json.dumps(token_usage), message[:100],
            )

        except Exception as e:
            logger.error("chat error session=%s error=%s", session_id, str(e))
            yield "data: " + json.dumps({"error": f"Server error: {str(e)}"}) + "\n\n"
        finally:
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


@app.route("/api/cancel", methods=["POST"])
@require_auth
def cancel():
    data = request.get_json() or {}
    request_id = data.get("request_id")
    if request_id and request_id in active_requests:
        active_requests[request_id] = False
        return jsonify({"cancelled": True})
    return jsonify({"cancelled": False})


@app.route("/api/clear", methods=["POST"])
@require_auth
def clear():
    return jsonify({"session_id": str(uuid.uuid4())})


@app.route("/api/feedback", methods=["POST"])
@require_auth
def feedback():
    data = request.get_json() or {}
    rating = data.get("rating")
    if rating not in ("up", "down"):
        return jsonify({"error": "Rating must be 'up' or 'down'"}), 400

    entry = {
        "session_id": data.get("session_id", ""),
        "message_index": data.get("message_index", -1),
        "rating": rating,
        "user_message": data.get("user_message", "")[:500],
        "assistant_message": data.get("assistant_message", "")[:500],
        "timestamp": time.time(),
    }
    feedback_store.append(entry)
    logger.info("feedback session=%s rating=%s", entry["session_id"], rating)
    return jsonify({"saved": True})


@app.route("/api/paginate", methods=["POST"])
@require_auth
def paginate():
    data = request.get_json() or {}
    query_id = data.get("query_id")
    page = data.get("page", 2)

    if not query_id:
        return jsonify({"error": "query_id is required"}), 400

    result = agent.paginate(query_id, page)
    if "error" in result:
        return jsonify(result), 400

    return jsonify(result)


@app.route("/api/health", methods=["GET"])
def health():
    checks = {"status": "ok", "timestamp": time.time()}
    try:
        agent.db.run("SELECT 1")
        checks["database"] = "connected"
    except Exception as e:
        checks["database"] = f"error: {str(e)}"
        checks["status"] = "degraded"

    checks["llm"] = "configured" if os.getenv("ANTHROPIC_API_KEY") else "missing"
    if checks["llm"] == "missing":
        checks["status"] = "degraded"

    status_code = 200 if checks["status"] == "ok" else 503
    return jsonify(checks), status_code


if __name__ == "__main__":
    logger.info("Starting TMS AI Chatbot on port 5000")
    app.run(debug=True, threaded=True, port=5000)
