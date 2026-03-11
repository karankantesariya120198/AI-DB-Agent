import os
import json
import uuid
import yaml
from flask import Flask, request, Response, render_template
from agent import create_agent_instance

app = Flask(__name__)

# Load domain config for UI values
config_path = os.getenv("AGENT_CONFIG", "config.yaml")
with open(config_path, "r") as f:
    app_config = yaml.safe_load(f)

# Module-level agent singleton
agent = create_agent_instance(config_path)


@app.route("/")
def index():
    domain = app_config.get("domain", {})
    return render_template(
        "index.html",
        title=domain.get("name", "AI Agent"),
        icon=domain.get("icon", "&#x1F916;"),
        description=domain.get("description", "Ask questions about your data"),
        placeholder=domain.get("placeholder", "Ask a question..."),
    )


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    message = data.get("message", "").strip()
    session_id = data.get("session_id", str(uuid.uuid4()))

    if not message:
        return Response("data: " + json.dumps({"error": "Empty message"}) + "\n\n",
                        content_type="text/event-stream")

    def generate():
        # Signal that the agent is thinking
        yield "data: " + json.dumps({"status": "thinking"}) + "\n\n"

        # Call agent synchronously
        result = agent.query(message, thread_id=session_id)
        full_response = result["response"]

        # Stream response word-by-word
        words = full_response.split(" ")
        for i, word in enumerate(words):
            token = word + ("" if i == len(words) - 1 else " ")
            yield "data: " + json.dumps({"word": token}) + "\n\n"

        # Final event with full response
        yield "data: " + json.dumps({"done": True, "full_response": full_response}) + "\n\n"

    return Response(generate(), content_type="text/event-stream")


@app.route("/api/clear", methods=["POST"])
def clear():
    new_session_id = str(uuid.uuid4())
    return {"session_id": new_session_id}


if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5000)
