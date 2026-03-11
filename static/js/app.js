const chatContainer = document.getElementById("chat-container");
const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");
const clearBtn = document.getElementById("clear-btn");

let sessionId = crypto.randomUUID();

function scrollToBottom() {
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function createBubble(role, content) {
    const div = document.createElement("div");
    div.className = `message ${role}`;
    if (role === "user") {
        div.textContent = content;
    } else {
        div.innerHTML = content;
    }
    chatContainer.appendChild(div);
    scrollToBottom();
    return div;
}

function renderMarkdown(text) {
    // Use marked.js to render markdown (including tables)
    if (typeof marked !== "undefined") {
        return marked.parse(text);
    }
    // Fallback: escape HTML and convert newlines
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\n/g, "<br>");
}

chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = messageInput.value.trim();
    if (!message) return;

    // Show user message
    createBubble("user", message);
    messageInput.value = "";
    sendBtn.disabled = true;

    // Create assistant bubble with spinner
    const assistantBubble = createBubble(
        "assistant",
        '<span class="spinner"></span> Thinking...'
    );

    let streamedText = "";

    try {
        const response = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message, session_id: sessionId }),
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop(); // Keep incomplete line in buffer

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const jsonStr = line.slice(6);
                if (!jsonStr) continue;

                let event;
                try {
                    event = JSON.parse(jsonStr);
                } catch {
                    continue;
                }

                if (event.status === "thinking") {
                    // Already showing spinner
                    continue;
                }

                if (event.word !== undefined) {
                    if (streamedText === "") {
                        // Clear spinner on first word
                        assistantBubble.innerHTML = "";
                    }
                    streamedText += event.word;
                    assistantBubble.textContent = streamedText;
                    scrollToBottom();
                }

                if (event.done) {
                    // Render final response with markdown (tables, formatting)
                    assistantBubble.innerHTML = renderMarkdown(
                        event.full_response
                    );
                    scrollToBottom();
                }

                if (event.error) {
                    assistantBubble.innerHTML =
                        '<span style="color:red;">Error: ' +
                        event.error +
                        "</span>";
                }
            }
        }
    } catch (err) {
        assistantBubble.innerHTML =
            '<span style="color:red;">Connection error. Please try again.</span>';
    }

    sendBtn.disabled = false;
    messageInput.focus();
});

clearBtn.addEventListener("click", async () => {
    try {
        const res = await fetch("/api/clear", { method: "POST" });
        const data = await res.json();
        sessionId = data.session_id;
    } catch {
        sessionId = crypto.randomUUID();
    }
    chatContainer.innerHTML = "";
    messageInput.focus();
});

// Focus input on load
messageInput.focus();
