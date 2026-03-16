"""
Query-based engine for the TMS chatbot.

Classifies user intent with a single cheap LLM call, executes a pre-defined
SQL template, and formats the response — bypassing the multi-step LangChain
agent for common queries.  Falls back to the full agent for unknown intents.
"""

import os
import time
import hashlib
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Any, List, Optional, Tuple, Union

import anthropic
from sqlalchemy import text as sa_text

from config import (
    CLASSIFY_MAX_TOKENS,
    CLASSIFY_MODEL,
    CONFIDENCE_THRESHOLD,
    CONTEXT_CLEANUP_INTERVAL,
    ENTITY_KEYS,
    FORMAT_MAX_TOKENS,
    FORMAT_MODEL,
    PAGE_SIZE,
    QUERY_TIMEOUT,
    SESSION_TTL,
)
from helper import get_logger, execute_with_timeout, normalize_sql
from tracx_engine.templates import TEMPLATES, QueryTemplate, DETAIL_INTENTS

logger = get_logger("query_engine")


@dataclass
class SessionContext:
    """Lightweight per-session entity store for conversation context."""

    entities: Dict[str, Any] = field(default_factory=dict)
    last_intent: str = ""
    updated_at: float = field(default_factory=time.time)

    def update(self, intent: str, params: Dict[str, Any]) -> None:
        """Merge new params into stored entities."""
        for key, value in params.items():
            if key in ENTITY_KEYS and value is not None:
                self.entities[key] = value
        self.last_intent = intent
        self.updated_at = time.time()

    def is_expired(self, ttl: float = SESSION_TTL) -> bool:
        return (time.time() - self.updated_at) > ttl

    def to_prompt_block(self) -> str:
        """Render entities as a compact string for the classifier prompt."""
        if not self.entities:
            return ""
        lines = [f"- {k}: {v}" for k, v in self.entities.items()]
        if self.last_intent:
            lines.append(f"- last_intent: {self.last_intent}")
        return "\n".join(lines)

    def clear(self) -> None:
        self.entities.clear()
        self.last_intent = ""
        self.updated_at = time.time()


class QueryEngine:
    """Intent-classify → template-execute → format pipeline with LLM fallback."""

    def __init__(self, agent, org_id: int):
        """
        Parameters
        ----------
        agent : DatabaseAgent
            The existing LangChain-based agent used as fallback.
        org_id : int
            Organisation ID injected into every query.
        """
        self.agent = agent
        self.org_id = org_id
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        # Re-use agent's infrastructure
        self.query_cache = agent.query_cache

        # Pagination state (mirrors DatabaseAgent._last_pagination)
        self._last_pagination: Optional[Dict] = None

        # Conversation context memory
        self._session_contexts: Dict[str, SessionContext] = {}
        self._query_count: int = 0

        # Build intent list for classifier prompt
        self._intent_descriptions = "\n".join(
            f"- {t.intent}: {t.description}" for t in TEMPLATES.values()
        )
        self._intent_enum = list(TEMPLATES.keys()) + ["unknown"]

    # ------------------------------------------------------------------
    # Public API (same interface as DatabaseAgent)
    # ------------------------------------------------------------------
    def query(self, user_input: str, thread_id: str = "default") -> Dict[str, Any]:
        """Main entry point — try template, fall back to LLM agent."""
        start = time.time()
        context = self._get_context(thread_id)

        # Periodic cleanup of expired sessions
        self._query_count += 1
        if self._query_count % CONTEXT_CLEANUP_INTERVAL == 0:
            self._cleanup_expired_contexts()

        try:
            classification = self.classify_intent(user_input, context=context)
            intent = classification.get("intent", "unknown")
            confidence = classification.get("confidence", 0)

            if intent != "unknown" and intent in TEMPLATES and confidence >= CONFIDENCE_THRESHOLD:
                params = classification.get("params", {})
                logger.info(
                    "Template hit: intent=%s confidence=%.2f params=%s",
                    intent, confidence, params,
                )

                data = self.execute_template(intent, params)
                if "error" in data:
                    # Template execution failed — fall back
                    logger.warning("Template exec failed, falling back: %s", data["error"])
                    return self._fallback(user_input, thread_id)

                # Seed token_usage with classify call tokens
                classify_usage = classification.get("_classify_usage", {})
                data["token_usage"] = {
                    "classify_input_tokens": classify_usage.get("input_tokens", 0),
                    "classify_output_tokens": classify_usage.get("output_tokens", 0),
                    "total_input_tokens": classify_usage.get("input_tokens", 0),
                    "total_output_tokens": classify_usage.get("output_tokens", 0),
                }

                response = self.format_response(intent, data, user_input)
                duration = time.time() - start

                # Pagination state
                pagination = self._last_pagination
                self._last_pagination = None

                # Update conversation context with extracted entities
                self._update_context(thread_id, intent, params)

                return {
                    "success": True,
                    "response": response,
                    "sql_queries": [data["sql"]],
                    "token_usage": data.get("token_usage", {}),
                    "pagination": pagination,
                    "source": "template",
                    "duration": round(duration, 2),
                }

            # Unknown intent → full agent
            logger.info("No template match (intent=%s, confidence=%.2f), using agent", intent, confidence)
            return self._fallback(user_input, thread_id)

        except Exception as e:
            logger.error("QueryEngine error, falling back: %s", e, exc_info=True)
            return self._fallback(user_input, thread_id)

    def paginate(self, query_id: str, page: int, page_size: int = 10) -> Dict:
        """Delegate pagination to the underlying agent."""
        return self.agent.paginate(query_id, page, page_size)

    # Expose agent attributes so app.py can use QueryEngine transparently
    @property
    def db(self):
        return self.agent.db

    @property
    def paginated_queries(self):
        return self.agent.paginated_queries

    def reset_conversation(self):
        self._session_contexts.clear()
        return self.agent.reset_conversation()

    def get_database_summary(self) -> Dict:
        return self.agent.get_database_summary()

    # ------------------------------------------------------------------
    # Session context helpers
    # ------------------------------------------------------------------
    def _get_context(self, thread_id: str) -> SessionContext:
        """Get or create a SessionContext for the given thread."""
        ctx = self._session_contexts.get(thread_id)
        if ctx is None or ctx.is_expired():
            ctx = SessionContext()
            self._session_contexts[thread_id] = ctx
        return ctx

    def _update_context(self, thread_id: str, intent: str, params: Dict[str, Any]) -> None:
        """Update context after a successful query."""
        ctx = self._get_context(thread_id)
        ctx.update(intent, params)

    def _cleanup_expired_contexts(self) -> None:
        """Remove stale sessions to prevent memory leaks."""
        expired = [tid for tid, ctx in self._session_contexts.items() if ctx.is_expired()]
        for tid in expired:
            del self._session_contexts[tid]
        if expired:
            logger.debug("Cleaned up %d expired session contexts", len(expired))

    # ------------------------------------------------------------------
    # Intent classification
    # ------------------------------------------------------------------
    def classify_intent(self, message: str, context: SessionContext = None) -> Dict[str, Any]:
        """Single LLM call to classify intent + extract params."""
        today = date.today().isoformat()

        tools = [{
            "name": "classify",
            "description": "Classify the user message into a TMS query intent and extract parameters.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": self._intent_enum,
                        "description": "The matched intent, or 'unknown' if no template fits.",
                    },
                    "params": {
                        "type": "object",
                        "description": "Extracted parameters for the intent. Keys must match the template's param names.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score 0-1 for the classification.",
                    },
                },
                "required": ["intent", "params", "confidence"],
            },
        }]

        system_prompt = f"""You classify user messages about a TMS (Transportation Management System) into query intents.
Today's date is {today}.

Available intents:
{self._intent_descriptions}
- unknown: The message doesn't match any of the above intents, or is too complex/ambiguous.

Rules:
- Extract parameter values from the user's message.
- For string parameters used in LIKE queries (customer_name, driver_name, equipment_name, location, etc.), provide just the name without wildcards.
- For date parameters, convert relative dates to absolute YYYY-MM-DD format. "today" = {today}, "yesterday" = {(date.today() - timedelta(days=1)).isoformat()}, etc.
- For "today's loads" or "loads shipped today", use get_loads_by_date with start_date and end_date both set to today.
- For limit parameters, default to 10 if not specified.
- If the user asks something off-topic (not about loads, drivers, customers, equipment, settlements), return unknown.
- If the message is ambiguous between multiple intents, pick the most likely one.
- Return confidence < {CONFIDENCE_THRESHOLD} if you're unsure.
- Always call the classify tool with your answer.
- When the user uses pronouns like "their", "them", "that customer", "that driver", resolve them using the conversation context below.
- When a pronoun is ambiguous (e.g. "their loads" and context has both a customer and driver), prefer the entity most related to the last_intent."""

        if context:
            context_block = context.to_prompt_block()
            if context_block:
                system_prompt += f"\n\nConversation context (previously discussed entities):\n{context_block}"

        try:
            resp = self.client.messages.create(
                model=CLASSIFY_MODEL,
                max_tokens=CLASSIFY_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": message}],
                tools=tools,
                tool_choice={"type": "tool", "name": "classify"},
            )

            # Extract the tool_use block
            for block in resp.content:
                if block.type == "tool_use" and block.name == "classify":
                    result = block.input
                    # Attach classification token usage for downstream merging
                    result["_classify_usage"] = {
                        "input_tokens": resp.usage.input_tokens,
                        "output_tokens": resp.usage.output_tokens,
                    }
                    return result

            return {"intent": "unknown", "params": {}, "confidence": 0}

        except Exception as e:
            logger.error("Classification failed: %s", e)
            return {"intent": "unknown", "params": {}, "confidence": 0}

    # ------------------------------------------------------------------
    # Template execution
    # ------------------------------------------------------------------
    def execute_template(self, intent: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fill params into SQL template and execute safely."""
        template = TEMPLATES[intent]

        # Apply defaults for optional params
        bound_params: Dict[str, Any] = {"org_id": self.org_id}
        for pname, pdef in template.params.items():
            value = params.get(pname)
            if value is None:
                if pdef.get("required", False):
                    return {"error": f"Missing required parameter: {pname}"}
                value = pdef.get("default")
            if value is None:
                continue

            # Wrap LIKE params with wildcards
            if pdef.get("type") == "string":
                # Check if this param is used in a LIKE clause
                if f"LIKE :{pname}" in template.sql:
                    value = f"%{value}%"

            bound_params[pname] = value

        # Build the actual SQL string for logging/display
        sql_text = template.sql.strip()

        # Check cache
        cache_key = hashlib.md5((sql_text + str(sorted(bound_params.items()))).encode()).hexdigest()
        cached = self.query_cache.get(cache_key)
        if cached is not None:
            logger.info("Cache hit for template %s", intent)
            return cached

        # Execute with pagination probe
        start = time.time()
        result = self._execute_parameterized(sql_text, bound_params)
        duration = time.time() - start

        if isinstance(result, str):
            return {"error": result}

        columns, rows = result
        logger.info("Template %s executed in %.2fs (%d rows)", intent, duration, len(rows))

        data = {
            "columns": columns,
            "rows": rows,
            "sql": self._render_sql_for_display(sql_text, bound_params),
            "total_rows": len(rows),
            "token_usage": {},
        }

        self.query_cache.set(cache_key, data)
        return data

    def _execute_parameterized(
        self, sql: str, params: Dict[str, Any]
    ) -> Union[Tuple[List[str], List[list]], str]:
        """Execute parameterized SQL with timeout and pagination."""
        # Probe with LIMIT for pagination
        probe_sql = normalize_sql(sql) + f" LIMIT {PAGE_SIZE + 1}"

        def run_query():
            with self.agent.db._engine.connect() as conn:
                cursor = conn.execute(sa_text(probe_sql), params)
                columns = list(cursor.keys())
                rows = [list(r) for r in cursor.fetchall()]
            return (columns, rows)

        result = execute_with_timeout(
            run_query,
            timeout=QUERY_TIMEOUT,
            timeout_msg=f"Query timed out after {QUERY_TIMEOUT} seconds.",
        )

        if isinstance(result, str):
            return result

        columns, rows = result
        has_more = len(rows) > PAGE_SIZE
        display_rows = rows[:PAGE_SIZE]

        if has_more:
            qid = str(_uuid.uuid4())
            # Store the un-limited SQL for pagination
            self.agent.paginated_queries[qid] = {
                "sql": self._render_sql_for_execution(sql, params),
                "columns": columns,
            }
            self._last_pagination = {
                "query_id": qid,
                "has_more": True,
                "page": 1,
                "page_size": PAGE_SIZE,
                "columns": columns,
            }
        else:
            self._last_pagination = None

        return (columns, display_rows)

    @staticmethod
    def _render_sql_for_display(sql: str, params: Dict[str, Any]) -> str:
        """Render SQL with params inlined for display/logging only."""
        rendered = sql
        for key, value in params.items():
            if isinstance(value, str):
                rendered = rendered.replace(f":{key}", f"'{value}'")
            elif value is not None:
                rendered = rendered.replace(f":{key}", str(value))
        return rendered

    @staticmethod
    def _render_sql_for_execution(sql: str, params: Dict[str, Any]) -> str:
        """Render SQL with params inlined for pagination storage.

        The pagination system in DatabaseAgent.paginate() expects raw SQL
        (no :param placeholders), so we inline the values.
        """
        rendered = sql
        for key, value in params.items():
            placeholder = f":{key}"
            if isinstance(value, str):
                # Escape single quotes
                safe_val = value.replace("'", "''")
                rendered = rendered.replace(placeholder, f"'{safe_val}'")
            elif isinstance(value, (int, float)):
                rendered = rendered.replace(placeholder, str(value))
            elif value is not None:
                rendered = rendered.replace(placeholder, f"'{str(value)}'")
        return rendered

    # ------------------------------------------------------------------
    # Response formatting
    # ------------------------------------------------------------------

    def format_response(self, intent: str, data: Dict[str, Any], user_message: str) -> str:
        """Format query results into markdown — bullet points for detail
        views (single-row / single-entity), tables for lists.
        Then wrap in a conversational LLM response."""
        template = TEMPLATES[intent]
        columns = data["columns"]
        rows = data["rows"]
        total_rows = data.get("total_rows", len(rows))

        if not rows:
            return f"No results found for your query. I searched for {template.description.lower()} but nothing matched."

        # Single-row or detail intent → bullet-point card
        if len(rows) <= 3 and intent in DETAIL_INTENTS:
            raw = self._format_as_bullets(columns, rows)
        else:
            # Multi-row → table
            raw = self._format_as_table(columns, rows, total_rows)

        return self._llm_format(user_message, raw, template.response_hint, data)

    def _format_as_bullets(self, columns: list, rows: list) -> str:
        """Render each row as a bullet-point card."""
        parts = []
        for row in rows:
            lines = []
            for col, val in zip(columns, row):
                display = str(val) if val is not None and str(val) != "None" else "—"
                lines.append(f"- **{col}:** {display}")
            parts.append("\n".join(lines))
        return "\n\n---\n\n".join(parts)

    def _format_as_table(self, columns: list, rows: list, total_rows: int) -> str:
        """Render rows as a markdown table."""
        md_lines = []

        # Header
        md_lines.append("| " + " | ".join(str(c) for c in columns) + " |")
        md_lines.append("| " + " | ".join("---" for _ in columns) + " |")

        # Rows
        for row in rows:
            cells = []
            for v in row:
                cell = str(v) if v is not None else ""
                cell = cell.replace("|", "\\|")
                cells.append(cell)
            md_lines.append("| " + " | ".join(cells) + " |")

        table = "\n".join(md_lines)

        # Summary
        showing = len(rows)
        if self._last_pagination and self._last_pagination.get("has_more"):
            summary = f"Showing {showing} of many results (more available)."
        else:
            summary = f"Found {total_rows} result{'s' if total_rows != 1 else ''}."

        return f"{summary}\n\n{table}"

    # ------------------------------------------------------------------
    # LLM formatting — wrap raw data in conversational response
    # ------------------------------------------------------------------
    def _llm_format(
        self, user_message: str, pre_formatted: str, response_hint: str, data: Dict[str, Any]
    ) -> str:
        """Use a cheap Haiku call to wrap pre-formatted data in natural language.

        Falls back to the raw pre-formatted data if the LLM call fails.
        Merges token usage into data["token_usage"] for the frontend status bar.
        """
        system_prompt = (
            "You are a helpful TMS (Transportation Management System) assistant.\n"
            "The user asked a question and we retrieved the following data from the database.\n"
            "Present it in a friendly, conversational way.\n\n"
            "Rules:\n"
            "- Add a brief natural intro sentence (e.g. \"Here are the details for ...\")\n"
            "- Keep the data formatting exactly as provided (tables stay as tables, bullets stay as bullets)\n"
            "- Do NOT add data that isn't in the provided results\n"
            "- Do NOT reformat tables into bullets or vice versa\n"
            "- You may add a short closing line if appropriate\n"
            "- Keep it concise — no filler\n\n"
            f"Hint: {response_hint}\n\n"
            f"Data:\n{pre_formatted}"
        )

        try:
            resp = self.client.messages.create(
                model=FORMAT_MODEL,
                max_tokens=FORMAT_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )

            # Merge token usage
            usage = resp.usage
            token_info = data.get("token_usage", {})
            token_info["format_input_tokens"] = usage.input_tokens
            token_info["format_output_tokens"] = usage.output_tokens
            token_info["total_input_tokens"] = token_info.get("total_input_tokens", 0) + usage.input_tokens
            token_info["total_output_tokens"] = token_info.get("total_output_tokens", 0) + usage.output_tokens
            data["token_usage"] = token_info

            # Extract text response
            text = resp.content[0].text if resp.content else pre_formatted
            return text

        except Exception as e:
            logger.warning("LLM formatting failed, returning raw data: %s", e)
            return pre_formatted

    # ------------------------------------------------------------------
    # Fallback to LangChain agent
    # ------------------------------------------------------------------
    def _fallback(self, user_input: str, thread_id: str) -> Dict[str, Any]:
        """Route to the existing DatabaseAgent."""
        context = self._get_context(thread_id)
        if context.entities:
            entity_hint = ", ".join(f"{k}={v}" for k, v in context.entities.items())
            user_input = f"[Context: {entity_hint}] {user_input}"
        result = self.agent.query(user_input, thread_id)
        result["source"] = "agent"
        return result
