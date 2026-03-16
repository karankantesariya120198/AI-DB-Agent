import os
import threading
import time
from typing import Any

from flask import Flask, Response, g, request
from flask_cors import CORS

from agent import create_agent_instance
from config import APP_PORT
from helper import get_logger, load_yaml_config
from routes import bp, limiter

logger = get_logger("app")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def create_app() -> Flask:
    app = Flask(__name__)

    # CORS — configurable origins via env var
    CORS(app, origins=os.getenv("CORS_ORIGINS", "*").split(","))

    # Rate limiter (instance lives in routes.py, bound to this app here)
    limiter.init_app(app)

    # Load domain config for UI values
    config_path = os.path.join("domains", os.getenv("AGENT_CONFIG", "config.yaml"))
    app.config["DOMAIN_CONFIG"] = load_yaml_config(config_path)

    # Agent singleton
    app.config["AGENT"] = create_agent_instance(config_path)

    # In-memory feedback store (swap for DB in production)
    app.config["FEEDBACK_STORE"]: list[dict[str, Any]] = []
    app.config["FEEDBACK_LOCK"] = threading.Lock()

    # Active request tracking for cancellation
    app.config["ACTIVE_REQUESTS"]: dict[str, bool] = {}
    app.config["REQUEST_LOCK"] = threading.Lock()

    # -------------------------------------------------------------------
    # Request timing & logging
    # -------------------------------------------------------------------
    @app.before_request
    def before_request_hook() -> None:
        g.start_time = time.time()

    @app.after_request
    def after_request_hook(response: Response) -> Response:
        if hasattr(g, "start_time"):
            duration = time.time() - g.start_time
            logger.info(
                "request=%s %s status=%s duration=%.3fs",
                request.method, request.path, response.status_code, duration,
            )
        return response

    # Register routes
    app.register_blueprint(bp)

    return app


if __name__ == "__main__":
    logger.info("Starting TMS AI Chatbot on port %d", APP_PORT)
    app = create_app()
    app.run(debug=True, threaded=True, port=APP_PORT)
