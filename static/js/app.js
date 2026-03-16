/* ===================================================================
   TMS AI Chatbot — Frontend
   =================================================================== */

const chatContainer = document.getElementById("chat-container");
const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");
const stopBtn = document.getElementById("stop-btn");
const clearBtn = document.getElementById("clear-btn");
const themeToggle = document.getElementById("theme-toggle");
const searchToggle = document.getElementById("search-toggle");
const searchBar = document.getElementById("search-bar");
const searchInput = document.getElementById("search-input");
const searchCount = document.getElementById("search-count");
const searchClose = document.getElementById("search-close");
const tokenInfo = document.getElementById("token-info");

let sessionId = localStorage.getItem("chat_session_id") || crypto.randomUUID();
localStorage.setItem("chat_session_id", sessionId);

let currentRequestId = null;
let abortController = null;
let messageIndex = 0;

/* -------------------------------------------------------------------
   Theme (Dark Mode)
   ------------------------------------------------------------------- */
function initTheme() {
    const saved = localStorage.getItem("theme") || "light";
    document.documentElement.setAttribute("data-theme", saved);
}
initTheme();

themeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
});

/* -------------------------------------------------------------------
   Helpers
   ------------------------------------------------------------------- */
function scrollToBottom() {
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function formatTime(date) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

/* -------------------------------------------------------------------
   Chat History Persistence (localStorage)
   ------------------------------------------------------------------- */
const STORAGE_KEY = "chat_history";

function saveHistory() {
    const messages = [];
    chatContainer.querySelectorAll(".message-row").forEach((row) => {
        const bubble = row.querySelector(".message");
        const time = row.querySelector(".message-time");
        if (!bubble) return;
        const role = row.classList.contains("user") ? "user" : "assistant";
        messages.push({
            role,
            html: bubble.innerHTML,
            text: bubble.textContent,
            time: time ? time.textContent : "",
        });
    });
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
        // localStorage full — silently ignore
    }
}

function restoreHistory() {
    try {
        const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
        if (!data.length) return false;
        data.forEach((msg) => {
            const wrapper = document.createElement("div");
            wrapper.className = `message-row ${msg.role}`;

            // Avatar
            const avatar = document.createElement("div");
            avatar.className = `avatar ${msg.role}`;
            avatar.textContent = msg.role === "user" ? "U" : "AI";
            avatar.setAttribute("aria-hidden", "true");

            const div = document.createElement("div");
            div.className = `message ${msg.role}`;
            div.innerHTML = msg.html;

            const inner = document.createElement("div");
            inner.className = "message-inner";
            inner.appendChild(avatar);
            inner.appendChild(div);
            wrapper.appendChild(inner);

            if (msg.time) {
                const timeEl = document.createElement("span");
                timeEl.className = `message-time ${msg.role}`;
                timeEl.textContent = msg.time;
                wrapper.appendChild(timeEl);
            }
            chatContainer.appendChild(wrapper);
        });
        // Re-apply syntax highlighting & interactive buttons
        chatContainer.querySelectorAll("pre code.language-sql").forEach((block) => {
            if (typeof Prism !== "undefined") Prism.highlightElement(block);
        });
        addInteractiveButtons();
        scrollToBottom();
        messageIndex = data.length;
        return true;
    } catch {
        return false;
    }
}

/* -------------------------------------------------------------------
   Welcome Message & Suggestion Chips
   ------------------------------------------------------------------- */
function showWelcome() {
    const prompts = window.__SUGGESTED_PROMPTS__ || [];
    const wrapper = document.createElement("div");
    wrapper.className = "message-row assistant welcome-row";
    wrapper.id = "welcome-message";

    let chipsHtml = "";
    if (prompts.length) {
        chipsHtml =
            '<div class="suggestion-chips">' +
            prompts.map((p) => `<button class="chip" data-prompt="${escapeHtml(p)}">${escapeHtml(p)}</button>`).join("") +
            "</div>";
    }

    wrapper.innerHTML = `
        <div class="message assistant welcome-bubble">
            <p>Hi! I can help you query your database using natural language.</p>
            <p>Try one of these suggestions or type your own question:</p>
            ${chipsHtml}
        </div>
        <span class="message-time assistant">${formatTime(new Date())}</span>
    `;

    chatContainer.appendChild(wrapper);

    // Chip click handlers
    wrapper.querySelectorAll(".chip").forEach((chip) => {
        chip.addEventListener("click", () => {
            messageInput.value = chip.dataset.prompt;
            chatForm.dispatchEvent(new Event("submit"));
        });
    });

    scrollToBottom();
}

/* -------------------------------------------------------------------
   Bubble creation
   ------------------------------------------------------------------- */
function addTimeToWrapper(bubbleEl, role) {
    // bubbleEl is .message, its parent is .message-inner, we need .message-row
    const row = bubbleEl.closest(".message-row");
    if (row && !row.querySelector(".message-time")) {
        const timeEl = document.createElement("span");
        timeEl.className = `message-time ${role}`;
        timeEl.textContent = formatTime(new Date());
        row.appendChild(timeEl);
    }
}

function createBubble(role, content, showTime = true) {
    const wrapper = document.createElement("div");
    wrapper.className = `message-row ${role}`;
    wrapper.setAttribute("data-index", messageIndex++);

    // Avatar
    const avatar = document.createElement("div");
    avatar.className = `avatar ${role}`;
    avatar.textContent = role === "user" ? "U" : "AI";
    avatar.setAttribute("aria-hidden", "true");

    const div = document.createElement("div");
    div.className = `message ${role}`;
    if (role === "user") {
        div.textContent = content;
    } else {
        div.innerHTML = content;
    }

    const inner = document.createElement("div");
    inner.className = "message-inner";
    inner.appendChild(avatar);
    inner.appendChild(div);
    wrapper.appendChild(inner);

    if (showTime) {
        const timeEl = document.createElement("span");
        timeEl.className = `message-time ${role}`;
        timeEl.textContent = formatTime(new Date());
        wrapper.appendChild(timeEl);
    }

    chatContainer.appendChild(wrapper);
    scrollToBottom();
    return div;
}

/* -------------------------------------------------------------------
   Markdown rendering with Prism syntax highlighting
   ------------------------------------------------------------------- */
function renderMarkdown(text) {
    let html;
    if (typeof marked !== "undefined") {
        html = marked.parse(text);
    } else {
        html = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\n/g, "<br>");
    }

    // Wrap tables in scrollable container (handle any attributes on <table>)
    html = html.replace(/<table[^>]*>/gi, '<div class="table-wrapper">$&');
    html = html.replace(/<\/table>/gi, "</table></div>");

    return html;
}

function highlightCode(container) {
    if (typeof Prism !== "undefined") {
        container.querySelectorAll("pre code").forEach((block) => {
            Prism.highlightElement(block);
        });
    }
}

/* -------------------------------------------------------------------
   Interactive buttons: Copy, CSV export, SQL toggle, Feedback, Regenerate
   ------------------------------------------------------------------- */
function copyToClipboard(text) {
    // Use modern API if available, fall back to execCommand
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    }
    return fallbackCopy(text);
}

function fallbackCopy(text) {
    return new Promise((resolve) => {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        resolve();
    });
}

function addInteractiveButtons() {
    // Add copy buttons to code blocks
    chatContainer.querySelectorAll(".message.assistant pre").forEach((pre) => {
        const oldCopy = pre.querySelector(".copy-btn");
        if (oldCopy) oldCopy.remove();
        const btn = document.createElement("button");
        btn.className = "copy-btn";
        btn.textContent = "Copy";
        btn.setAttribute("aria-label", "Copy code");
        btn.addEventListener("click", () => {
            const code = pre.querySelector("code")
                ? pre.querySelector("code").textContent
                : pre.textContent;
            copyToClipboard(code).then(() => {
                btn.textContent = "Copied!";
                setTimeout(() => (btn.textContent = "Copy"), 1500);
            });
        });
        pre.style.position = "relative";
        pre.appendChild(btn);
    });

    // Add copy + CSV export buttons to tables
    chatContainer.querySelectorAll(".message.assistant .table-wrapper").forEach((tw) => {
        // Remove any stale buttons (e.g. restored from localStorage without listeners)
        const old = tw.querySelector(".table-actions");
        if (old) old.remove();

        const actions = document.createElement("div");
        actions.className = "table-actions";

        // Copy table as TSV
        const copyBtn = document.createElement("button");
        copyBtn.className = "table-action-btn";
        copyBtn.textContent = "Copy Table";
        copyBtn.setAttribute("aria-label", "Copy table");
        copyBtn.addEventListener("click", () => {
            const table = tw.querySelector("table");
            if (!table) return;
            const tsv = tableToTSV(table);
            copyToClipboard(tsv).then(() => {
                copyBtn.textContent = "Copied!";
                setTimeout(() => (copyBtn.textContent = "Copy Table"), 1500);
            });
        });

        // Download CSV
        const csvBtn = document.createElement("button");
        csvBtn.className = "table-action-btn";
        csvBtn.textContent = "Download CSV";
        csvBtn.setAttribute("aria-label", "Download as CSV");
        csvBtn.addEventListener("click", () => {
            const table = tw.querySelector("table");
            if (!table) return;
            const csv = tableToCSV(table);
            const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "query_results.csv";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        });

        actions.appendChild(copyBtn);
        actions.appendChild(csvBtn);
        // Insert actions bar at top of table wrapper
        tw.insertBefore(actions, tw.firstChild);
    });
}

/* -------------------------------------------------------------------
   Server-Side Table Pagination — "Load More" fetches next page via API
   ------------------------------------------------------------------- */
function addPaginationButton(bubble, pagination) {
    if (!pagination || !pagination.has_more) return;

    // Find the last table-wrapper inside the bubble
    const tableWrapper = bubble.querySelector(".table-wrapper:last-of-type");
    if (!tableWrapper) return;

    const table = tableWrapper.querySelector("table");
    if (!table) return;

    let currentPage = pagination.page; // starts at 1

    const bar = document.createElement("div");
    bar.className = "table-pagination";

    const info = document.createElement("span");
    info.className = "pagination-info";
    info.textContent = `Showing ${pagination.page_size} rows`;

    const moreBtn = document.createElement("button");
    moreBtn.className = "table-action-btn pagination-btn";
    moreBtn.textContent = "Load More";

    moreBtn.addEventListener("click", async () => {
        moreBtn.disabled = true;
        moreBtn.textContent = "Loading...";

        try {
            const res = await fetch("/api/paginate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    query_id: pagination.query_id,
                    page: currentPage + 1,
                }),
            });
            const data = await res.json();

            if (data.error) {
                moreBtn.textContent = "Error — Try Again";
                moreBtn.disabled = false;
                return;
            }

            // Append rows to the table
            const tbody = table.querySelector("tbody") || table;
            data.rows.forEach((row) => {
                const tr = document.createElement("tr");
                row.forEach((cell) => {
                    const td = document.createElement("td");
                    td.textContent = cell;
                    tr.appendChild(td);
                });
                tbody.appendChild(tr);
            });

            currentPage = data.page;
            const totalShown = currentPage * pagination.page_size;
            info.textContent = `Showing ${totalShown} rows`;

            if (!data.has_more) {
                moreBtn.style.display = "none";
                info.textContent += " (all loaded)";
            } else {
                moreBtn.disabled = false;
                moreBtn.textContent = "Load More";
            }

            scrollToBottom();
            saveHistory();
        } catch {
            moreBtn.textContent = "Error — Try Again";
            moreBtn.disabled = false;
        }
    });

    bar.appendChild(info);
    bar.appendChild(moreBtn);
    tableWrapper.appendChild(bar);
}

function tableToTSV(table) {
    const rows = [];
    table.querySelectorAll("tr").forEach((tr) => {
        const cells = [];
        tr.querySelectorAll("th, td").forEach((cell) => cells.push(cell.textContent.trim()));
        rows.push(cells.join("\t"));
    });
    return rows.join("\n");
}

function tableToCSV(table) {
    const rows = [];
    table.querySelectorAll("tr").forEach((tr) => {
        const cells = [];
        tr.querySelectorAll("th, td").forEach((cell) => {
            let val = cell.textContent.trim();
            if (val.includes(",") || val.includes('"') || val.includes("\n")) {
                val = '"' + val.replace(/"/g, '""') + '"';
            }
            cells.push(val);
        });
        rows.push(cells.join(","));
    });
    return rows.join("\n");
}

/* -------------------------------------------------------------------
   Feedback (Thumbs Up/Down)
   ------------------------------------------------------------------- */
function addFeedbackButtons(wrapper, userMsg, assistantMsg, idx) {
    const fb = document.createElement("div");
    fb.className = "feedback-row";

    const upBtn = document.createElement("button");
    upBtn.className = "feedback-btn";
    upBtn.innerHTML = "&#x1F44D;";
    upBtn.title = "Good response";
    upBtn.setAttribute("aria-label", "Good response");

    const downBtn = document.createElement("button");
    downBtn.className = "feedback-btn";
    downBtn.innerHTML = "&#x1F44E;";
    downBtn.title = "Bad response";
    downBtn.setAttribute("aria-label", "Bad response");

    const regenBtn = document.createElement("button");
    regenBtn.className = "feedback-btn regen-btn";
    regenBtn.innerHTML = "&#x1F504;";
    regenBtn.title = "Regenerate response";
    regenBtn.setAttribute("aria-label", "Regenerate response");

    function sendFeedback(rating) {
        fetch("/api/feedback", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: sessionId,
                message_index: idx,
                rating,
                user_message: userMsg,
                assistant_message: assistantMsg.substring(0, 500),
            }),
        }).catch(() => {});
        upBtn.disabled = true;
        downBtn.disabled = true;
        if (rating === "up") {
            upBtn.classList.add("active");
        } else {
            downBtn.classList.add("active");
        }
    }

    upBtn.addEventListener("click", () => sendFeedback("up"));
    downBtn.addEventListener("click", () => sendFeedback("down"));

    regenBtn.addEventListener("click", () => {
        // Remove this assistant message row and re-send the user message
        const row = wrapper.closest(".message-row");
        if (row) row.remove();
        messageInput.value = userMsg;
        chatForm.dispatchEvent(new Event("submit"));
    });

    fb.appendChild(upBtn);
    fb.appendChild(downBtn);
    fb.appendChild(regenBtn);
    wrapper.appendChild(fb);
}

/* -------------------------------------------------------------------
   Chat submission
   ------------------------------------------------------------------- */
chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = messageInput.value.trim();
    if (!message) return;

    // Remove welcome message if present
    const welcome = document.getElementById("welcome-message");
    if (welcome) welcome.remove();

    // Show user message
    createBubble("user", message);
    messageInput.value = "";
    sendBtn.classList.add("hidden");
    stopBtn.classList.remove("hidden");
    messageInput.disabled = true;

    // Thinking bubble with animated dots
    const assistantBubble = createBubble(
        "assistant",
        '<div class="thinking-dots"><span></span><span></span><span></span></div>',
        false
    );

    let streamedText = "";
    let sqlQueries = [];
    abortController = new AbortController();

    try {
        const response = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message, session_id: sessionId }),
            signal: abortController.signal,
        });

        if (!response.ok) {
            const err = await response.json();
            assistantBubble.innerHTML =
                '<div class="error-message">' +
                '<span class="error-text">Error: ' + escapeHtml(err.error || "Request failed") + "</span>" +
                '<button class="retry-btn">Retry</button>' +
                "</div>";
            addTimeToWrapper(assistantBubble, "assistant");
            assistantBubble.querySelector(".retry-btn").addEventListener("click", () => {
                const row = assistantBubble.closest(".message-row");
                if (row) row.remove();
                messageInput.value = message;
                chatForm.dispatchEvent(new Event("submit"));
            });
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();

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
                    currentRequestId = event.request_id;
                    continue;
                }

                if (event.word !== undefined) {
                    if (streamedText === "") {
                        assistantBubble.innerHTML = "";
                    }
                    streamedText += event.word;
                    assistantBubble.textContent = streamedText;
                    scrollToBottom();
                }

                if (event.done) {
                    assistantBubble.innerHTML = renderMarkdown(event.full_response);
                    highlightCode(assistantBubble);
                    addTimeToWrapper(assistantBubble, "assistant");
                    sqlQueries = event.sql_queries || [];
                    addInteractiveButtons();

                    const messageRow = assistantBubble.closest(".message-row");

                    // Feedback + regenerate
                    addFeedbackButtons(messageRow, message, event.full_response, messageIndex);

                    // Pagination — only when backend says there are more rows
                    if (event.pagination && event.pagination.has_more) {
                        addPaginationButton(assistantBubble, event.pagination);
                    }

                    // Token info
                    const source = event.source === "template" ? "Template" : "Agent";
                    if (event.token_usage && (event.token_usage.input_tokens || event.token_usage.output_tokens)) {
                        tokenInfo.textContent = `${source} | Tokens: ${event.token_usage.input_tokens || 0} in / ${event.token_usage.output_tokens || 0} out | ${event.duration || 0}s`;
                    } else {
                        tokenInfo.textContent = `${source} | ${event.duration || 0}s`;
                    }

                    scrollToBottom();
                    saveHistory();
                }

                if (event.error) {
                    assistantBubble.innerHTML =
                        '<div class="error-message">' +
                        '<span class="error-text">Error: ' + escapeHtml(event.error) + "</span>" +
                        '<button class="retry-btn">Retry</button>' +
                        "</div>";
                    addTimeToWrapper(assistantBubble, "assistant");

                    assistantBubble.querySelector(".retry-btn").addEventListener("click", () => {
                        const row = assistantBubble.closest(".message-row");
                        if (row) row.remove();
                        messageInput.value = message;
                        chatForm.dispatchEvent(new Event("submit"));
                    });
                }
            }
        }
    } catch (err) {
        if (err.name === "AbortError") {
            assistantBubble.innerHTML = '<span class="cancelled-text">Response cancelled.</span>';
            addTimeToWrapper(assistantBubble, "assistant");
        } else {
            assistantBubble.innerHTML =
                '<div class="error-message">' +
                '<span class="error-text">Connection error. Please try again.</span>' +
                '<button class="retry-btn">Retry</button>' +
                "</div>";
            addTimeToWrapper(assistantBubble, "assistant");

            assistantBubble.querySelector(".retry-btn").addEventListener("click", () => {
                const row = assistantBubble.closest(".message-row");
                if (row) row.remove();
                messageInput.value = message;
                chatForm.dispatchEvent(new Event("submit"));
            });
        }
    }

    sendBtn.classList.remove("hidden");
    stopBtn.classList.add("hidden");
    messageInput.disabled = false;
    currentRequestId = null;
    abortController = null;
    messageInput.focus();
});

/* -------------------------------------------------------------------
   Stop / Cancel
   ------------------------------------------------------------------- */
stopBtn.addEventListener("click", () => {
    if (abortController) abortController.abort();
    if (currentRequestId) {
        fetch("/api/cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ request_id: currentRequestId }),
        }).catch(() => {});
    }
});

/* -------------------------------------------------------------------
   Clear Chat
   ------------------------------------------------------------------- */
clearBtn.addEventListener("click", async () => {
    try {
        const res = await fetch("/api/clear", { method: "POST" });
        const data = await res.json();
        sessionId = data.session_id;
        localStorage.setItem("chat_session_id", sessionId);
    } catch {
        sessionId = crypto.randomUUID();
        localStorage.setItem("chat_session_id", sessionId);
    }
    chatContainer.innerHTML = "";
    localStorage.removeItem(STORAGE_KEY);
    tokenInfo.textContent = "";
    messageIndex = 0;
    showWelcome();
    messageInput.focus();
});

/* -------------------------------------------------------------------
   Search Messages
   ------------------------------------------------------------------- */
searchToggle.addEventListener("click", () => {
    searchBar.classList.toggle("hidden");
    if (!searchBar.classList.contains("hidden")) {
        searchInput.focus();
    } else {
        clearSearch();
    }
});

searchClose.addEventListener("click", () => {
    searchBar.classList.add("hidden");
    clearSearch();
});

searchInput.addEventListener("input", () => {
    const query = searchInput.value.trim().toLowerCase();
    if (!query) {
        clearSearch();
        return;
    }
    let count = 0;
    chatContainer.querySelectorAll(".message").forEach((msg) => {
        const text = msg.textContent.toLowerCase();
        const row = msg.closest(".message-row");
        if (text.includes(query)) {
            row.classList.remove("search-hidden");
            row.classList.add("search-highlight");
            count++;
        } else {
            row.classList.add("search-hidden");
            row.classList.remove("search-highlight");
        }
    });
    searchCount.textContent = `${count} found`;
});

function clearSearch() {
    searchInput.value = "";
    searchCount.textContent = "";
    chatContainer.querySelectorAll(".message-row").forEach((row) => {
        row.classList.remove("search-hidden", "search-highlight");
    });
}

/* -------------------------------------------------------------------
   Keyboard Shortcuts
   ------------------------------------------------------------------- */
document.addEventListener("keydown", (e) => {
    // Ctrl+L: clear chat
    if (e.ctrlKey && e.key === "l") {
        e.preventDefault();
        clearBtn.click();
    }
    // Escape: clear input or close search
    if (e.key === "Escape") {
        if (!searchBar.classList.contains("hidden")) {
            searchBar.classList.add("hidden");
            clearSearch();
        } else {
            messageInput.value = "";
            messageInput.focus();
        }
    }
    // Ctrl+F: open search
    if (e.ctrlKey && e.key === "f") {
        e.preventDefault();
        searchBar.classList.remove("hidden");
        searchInput.focus();
    }
});

/* -------------------------------------------------------------------
   Initialize
   ------------------------------------------------------------------- */
const hasHistory = restoreHistory();
if (!hasHistory) {
    showWelcome();
}
messageInput.focus();
