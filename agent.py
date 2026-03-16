import os
import re
import time
import logging
import warnings
import hashlib
import threading
import uuid as _uuid

import yaml
import sqlparse
from sqlalchemy import text as sa_text
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langgraph.checkpoint.memory import MemorySaver
from typing import Dict, Any, List, Tuple, Optional, Union

load_dotenv()

logger = logging.getLogger("tms_chatbot.agent")


# ---------------------------------------------------------------------------
# Simple TTL cache for query results
# ---------------------------------------------------------------------------
class QueryCache:
    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._store: Dict[str, Any] = {}
        self._timestamps: Dict[str, float] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            if key in self._store:
                if time.time() - self._timestamps[key] < self.ttl:
                    return self._store[key]
                del self._store[key]
                del self._timestamps[key]
        return None

    def set(self, key: str, value: Any):
        with self._lock:
            self._store[key] = value
            self._timestamps[key] = time.time()

    def clear(self):
        with self._lock:
            self._store.clear()
            self._timestamps.clear()


# ---------------------------------------------------------------------------
# Database Agent
# ---------------------------------------------------------------------------
class DatabaseAgent:
    QUERY_TIMEOUT = 30  # seconds
    PAGE_SIZE = 10

    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self.query_cache = QueryCache(ttl=300)

        # Pagination: stores original (unlimited) queries keyed by query_id
        self.paginated_queries: Dict[str, Dict] = {}
        self._last_pagination: Optional[Dict] = None

        llm_config = self.config.get("llm", {})
        self.llm = ChatAnthropic(
            model=llm_config.get("model", "claude-sonnet-4-20250514"),
            temperature=llm_config.get("temperature", 0.1),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            max_tokens=llm_config.get("max_tokens", 4096),
        )

        self.setup_database()
        self.checkpointer = MemorySaver()
        self.setup_tools()
        self.agent = self._create_agent()

    def _load_config(self, config_path: str = None) -> dict:
        path = config_path or os.getenv("AGENT_CONFIG", "config.yaml")
        try:
            with open(path, "r") as f:
                config = yaml.safe_load(f)
            logger.info("Loaded config from: %s", path)
            return config
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Config file not found: {path}. "
                f"Create a config.yaml or set AGENT_CONFIG env var."
            )

    def setup_database(self):
        warnings.filterwarnings("ignore", message=".*Cannot correctly sort tables.*")

        try:
            db_type = os.getenv("DB_TYPE", "sqlite")

            if db_type == "sqlite":
                db_path = os.getenv("DB_PATH", "database.db")
                db_uri = f"sqlite:///{db_path}"
            elif db_type == "mysql":
                user = os.getenv("DB_USER")
                password = os.getenv("DB_PASSWORD")
                host = os.getenv("DB_HOST", "localhost")
                port = os.getenv("DB_PORT", "3306")
                database = os.getenv("DB_NAME")
                db_uri = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
            elif db_type == "postgresql":
                user = os.getenv("DB_USER")
                password = os.getenv("DB_PASSWORD")
                host = os.getenv("DB_HOST", "localhost")
                port = os.getenv("DB_PORT", "5432")
                database = os.getenv("DB_NAME")
                db_uri = f"postgresql://{user}:{password}@{host}:{port}/{database}"
            else:
                raise ValueError(f"Unsupported DB_TYPE: {db_type}")

            include_tables = self.config.get("database", {}).get("include_tables")
            if include_tables:
                self.db = SQLDatabase.from_uri(db_uri, include_tables=include_tables)
            else:
                self.db = SQLDatabase.from_uri(db_uri)

            self.dialect = self.db.dialect
            self.table_names = self.db.get_usable_table_names()

            logger.info("Connected to %s database", db_type)
            logger.info("Available tables: %s", ", ".join(self.table_names))

        except Exception as e:
            logger.error("Database connection error: %s", e)
            raise

    # -------------------------------------------------------------------
    # SQL validation & execution helpers
    # -------------------------------------------------------------------
    def _validate_sql(self, query: str) -> str:
        """Validate SQL query safety using sqlparse. Returns error string or empty if valid."""
        query_stripped = query.strip()

        # Block compound statements (multiple queries separated by ;)
        if ";" in query_stripped.rstrip(";"):
            return "UNSAFE: Multiple statements detected. Only single queries are allowed."

        # Parse with sqlparse
        parsed = sqlparse.parse(query_stripped)
        if not parsed:
            return "UNSAFE: Could not parse SQL query."

        stmt = parsed[0]
        stmt_type = stmt.get_type()

        if stmt_type and stmt_type != "SELECT" and stmt_type != "UNKNOWN":
            return f"UNSAFE: Only SELECT queries are allowed. Detected: {stmt_type}"

        # Check first meaningful token
        first_token = None
        for token in stmt.tokens:
            if not token.is_whitespace:
                first_token = token
                break

        if first_token and first_token.ttype is sqlparse.tokens.DML:
            if first_token.normalized != "SELECT":
                return f"UNSAFE: Query starts with {first_token.normalized}. Only SELECT is allowed."

        # Block dangerous keywords anywhere (including subqueries)
        dangerous = {
            "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE",
            "TRUNCATE", "EXEC", "EXECUTE", "GRANT", "REVOKE",
        }
        tokens_upper = query_stripped.upper()
        for kw in dangerous:
            if re.search(r"\b" + kw + r"\b", tokens_upper):
                return f"UNSAFE: Query contains {kw} operation."

        return ""

    def _execute_with_timeout(self, query: str) -> str:
        """Execute a SQL query with a timeout."""
        result = [None]
        error = [None]

        def run():
            try:
                result[0] = self.db.run(query)
            except Exception as e:
                error[0] = e

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=self.QUERY_TIMEOUT)

        if thread.is_alive():
            return f"Error: Query timed out after {self.QUERY_TIMEOUT} seconds. Please try a simpler query."

        if error[0]:
            return f"Error executing query: {str(error[0])}"

        return result[0]

    def _execute_structured(self, query: str) -> Union[Tuple[List[str], List[list]], str]:
        """Execute a query and return (columns, rows) or an error string."""
        result = [None]
        error = [None]

        def run():
            try:
                with self.db._engine.connect() as conn:
                    cursor = conn.execute(sa_text(query))
                    columns = list(cursor.keys())
                    rows = [list(r) for r in cursor.fetchall()]
                result[0] = (columns, rows)
            except Exception as e:
                error[0] = e

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=self.QUERY_TIMEOUT)

        if thread.is_alive():
            return f"Error: Query timed out after {self.QUERY_TIMEOUT} seconds."
        if error[0]:
            return f"Error executing query: {str(error[0])}"
        return result[0]

    @staticmethod
    def _has_limit(query: str) -> bool:
        """Check whether a SQL query already contains a LIMIT clause."""
        # Look for LIMIT that is NOT inside a subquery
        upper = query.strip().upper()
        # Simple heuristic: check after the last closing paren (skip subqueries)
        after_subqueries = upper.rsplit(")", 1)[-1] if ")" in upper else upper
        return bool(re.search(r"\bLIMIT\s+\d+", after_subqueries))

    # -------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------
    def setup_tools(self):
        self.sql_toolkit = SQLDatabaseToolkit(db=self.db, llm=self.llm)
        base_tools = self.sql_toolkit.get_tools()

        @tool
        def get_database_info() -> str:
            """
            Get comprehensive information about the database including:
            - Database type/dialect
            - Available tables
            - Table schemas
            Use this tool first to understand the database structure.
            """
            info = []
            info.append(f"Database Type: {self.dialect}")
            info.append(f"\nAvailable Tables ({len(self.table_names)}):")

            for table in self.table_names:
                table_info = self.db.get_table_info([table])
                table_info = table_info.replace("CREATE TABLE ", f"\nTable: ")
                table_info = table_info.replace("(\n", "\n")
                table_info = table_info.replace("\n)", "")
                info.append(table_info)

            return "\n".join(info)

        @tool
        def get_table_schema(table_name: str) -> str:
            """
            Get detailed schema information for a specific table.
            Input should be the exact table name.
            Use this tool when you need detailed column information for writing queries.
            """
            if table_name not in self.table_names:
                similar_tables = [
                    t for t in self.table_names if table_name.lower() in t.lower()
                ]
                if similar_tables:
                    return f"Table '{table_name}' not found. Did you mean: {', '.join(similar_tables)}"
                return f"Table '{table_name}' not found. Available tables: {', '.join(self.table_names)}"

            return self.db.get_table_info([table_name])

        @tool
        def execute_sql_query(query: str) -> str:
            """
            Execute a SQL query and return the results.
            Input should be a valid SQL query.
            WARNING: This tool should only be used for SELECT queries.
            Always validate the query is safe before executing.
            """
            validation_error = self._validate_sql(query)
            if validation_error:
                return validation_error

            # Check cache
            cache_key = hashlib.md5(query.strip().encode()).hexdigest()
            cached = self.query_cache.get(cache_key)
            if cached is not None:
                logger.info("Cache hit for query: %s", query[:80])
                return cached

            logger.info("Executing query: %s", query[:200])
            start = time.time()

            # --- Pagination: auto-limit queries without LIMIT --------
            if not self._has_limit(query):
                probe_query = query.rstrip().rstrip(";") + f" LIMIT {self.PAGE_SIZE + 1}"
                structured = self._execute_structured(probe_query)

                if isinstance(structured, str):
                    # Error — fall through to normal execution
                    logger.warning("Structured probe failed, falling back: %s", structured[:120])
                else:
                    columns, rows = structured
                    duration = time.time() - start
                    logger.info("Query executed in %.2fs (%d rows)", duration, len(rows))

                    has_more = len(rows) > self.PAGE_SIZE
                    display_rows = rows[: self.PAGE_SIZE]

                    # Format like db.run() so the LLM sees a familiar string
                    result_str = str([tuple(r) for r in display_rows])

                    if has_more:
                        qid = str(_uuid.uuid4())
                        self.paginated_queries[qid] = {
                            "sql": query,
                            "columns": columns,
                        }
                        self._last_pagination = {
                            "query_id": qid,
                            "has_more": True,
                            "page": 1,
                            "page_size": self.PAGE_SIZE,
                            "columns": columns,
                        }
                        result_str += f"\n(Showing first {self.PAGE_SIZE} rows — more available)"
                    else:
                        self._last_pagination = None

                    self.query_cache.set(cache_key, result_str)
                    return result_str

            # --- Normal execution (query already has LIMIT, or probe failed)
            result = self._execute_with_timeout(query)
            duration = time.time() - start
            logger.info("Query executed in %.2fs", duration)
            self._last_pagination = None

            if not str(result).startswith("Error"):
                self.query_cache.set(cache_key, result)

            return result

        @tool
        def check_query_safety(query: str) -> str:
            """
            Check if a SQL query is safe to execute.
            Returns a safety assessment.
            """
            validation_error = self._validate_sql(query)
            if validation_error:
                return validation_error
            return "SAFE: Query appears to be a read-only SELECT statement."

        tool_names = [t.name for t in base_tools]
        custom_tools = [
            get_database_info,
            get_table_schema,
            execute_sql_query,
            check_query_safety,
        ]
        self.tools = base_tools + [t for t in custom_tools if t.name not in tool_names]

    # -------------------------------------------------------------------
    # Agent creation
    # -------------------------------------------------------------------
    def _create_agent(self):
        domain_name = self.config.get("domain", {}).get("name", "AI Agent")
        restriction_msg = self.config.get(
            "domain_restriction_message",
            f"I can only help with questions about your {domain_name} data.",
        )

        query_vars = self.config.get("query_variables", {})
        if query_vars:
            filter_lines = [f"- {key} = {value}" for key, value in query_vars.items()]
            filter_instructions = (
                "CRITICAL: Every SQL query you generate MUST include a filter for these variables:\n"
                + "\n".join(filter_lines)
                + "\nAlways apply these filters in the WHERE clause of every query. Never omit them."
            )
        else:
            filter_instructions = ""

        views = self.config.get("views", {})
        if views:
            view_lines = []
            for view_name, view_config in views.items():
                desc = view_config.get("description", view_name)
                endpoint = view_config.get("endpoint", "")
                cols = view_config.get("grid_columns", [])
                dest_cols = view_config.get("destination_columns", [])
                col_list = ", ".join(
                    [f"{c['label']} ({c['field']})" + (f" -- {c['note']}" if c.get('note') else "") for c in cols]
                )
                dest_col_list = ", ".join(
                    [f"{c['label']} ({c['field']})" + (f" -- {c['note']}" if c.get('note') else "") for c in dest_cols]
                )
                view_lines.append(f"View: {desc} ({endpoint})")
                view_lines.append(f"  Load columns: {col_list}")
                if dest_col_list:
                    view_lines.append(f"  Destination columns: {dest_col_list}")
            view_columns_instructions = "\n".join(view_lines)
        else:
            view_columns_instructions = ""

        table_joins = self.config.get("table_joins", [])
        if table_joins:
            join_lines = [f"- {j['join']}  -- {j['purpose']}" for j in table_joins]
            table_join_instructions = "\n".join(join_lines)
        else:
            table_join_instructions = ""

        system_prompt = self.config["system_prompt"].format(
            table_names=", ".join(self.table_names),
            domain_name=domain_name,
            domain_restriction_message=restriction_msg,
            query_filter_instructions=filter_instructions,
            view_columns_instructions=view_columns_instructions,
            table_join_instructions=table_join_instructions,
            **{k: str(v) for k, v in query_vars.items()},
        )

        agent = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=system_prompt,
            checkpointer=self.checkpointer,
        )

        return agent

    # -------------------------------------------------------------------
    # Query execution
    # -------------------------------------------------------------------
    def query(self, user_input: str, thread_id: str = "default") -> Dict[str, Any]:
        max_retries = 2

        try:
            enhanced_input = user_input

            structure_keywords = [
                "table", "schema", "structure", "column", "field", "database",
            ]
            if any(keyword in user_input.lower() for keyword in structure_keywords):
                enhanced_input = (
                    f"First understand the database structure, then answer: {user_input}"
                )

            config = {"configurable": {"thread_id": thread_id}}

            response = self.agent.invoke(
                {"messages": [{"role": "user", "content": enhanced_input}]},
                config=config,
            )

            output = self._extract_text(response["messages"][-1].content)

            # Retry on SQL errors
            for _ in range(max_retries):
                if "Error executing query" not in output:
                    break
                retry_msg = f"The previous query failed with: {output}\nPlease fix the SQL and try again."
                response = self.agent.invoke(
                    {"messages": [{"role": "user", "content": retry_msg}]},
                    config=config,
                )
                output = self._extract_text(response["messages"][-1].content)

            sql_queries = self.extract_sql_queries(output)

            # Extract token usage if available
            token_usage = {}
            last_msg = response["messages"][-1]
            if hasattr(last_msg, "usage_metadata") and last_msg.usage_metadata:
                token_usage = {
                    "input_tokens": last_msg.usage_metadata.get("input_tokens", 0),
                    "output_tokens": last_msg.usage_metadata.get("output_tokens", 0),
                }

            # Grab pagination state set by the tool, then reset
            pagination = self._last_pagination
            self._last_pagination = None

            return {
                "success": True,
                "response": output,
                "sql_queries": sql_queries,
                "token_usage": token_usage,
                "pagination": pagination,
            }
        except Exception as e:
            logger.error("Agent query error: %s", e, exc_info=True)
            return {
                "success": False,
                "response": f"I encountered an error: {str(e)}. Please try rephrasing your question.",
                "error": str(e),
            }

    # -------------------------------------------------------------------
    # Pagination — fetch next page for a stored query
    # -------------------------------------------------------------------
    def paginate(self, query_id: str, page: int, page_size: int = 10) -> Dict:
        stored = self.paginated_queries.get(query_id)
        if not stored:
            return {"error": "Query not found or expired"}

        sql = stored["sql"]
        columns = stored["columns"]
        offset = (page - 1) * page_size

        validation_error = self._validate_sql(sql)
        if validation_error:
            return {"error": validation_error}

        paginated_sql = sql.rstrip().rstrip(";") + f" LIMIT {page_size + 1} OFFSET {offset}"
        structured = self._execute_structured(paginated_sql)

        if isinstance(structured, str):
            return {"error": structured}

        _, rows = structured
        has_more = len(rows) > page_size
        display_rows = rows[:page_size]

        # Convert every cell to string for JSON serialisation
        data = []
        for row in display_rows:
            data.append([
                str(v) if v is not None else ""
                for v in row
            ])

        return {
            "columns": columns,
            "rows": data,
            "page": page,
            "has_more": has_more,
        }

    @staticmethod
    def _extract_text(content) -> str:
        """Normalize LLM message content to a plain string.

        LangChain tool-calling agents can return content as a list of
        content blocks (e.g. [{"type": "text", "text": "..."}]) instead of
        a simple string.  This helper always returns a string.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
                else:
                    parts.append(str(block))
            return "\n".join(parts)
        return str(content)

    def extract_sql_queries(self, text: str) -> list:
        # First try fenced code blocks
        matches = re.findall(r"```sql\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
        if not matches:
            # Fallback: bare SELECT statements
            matches = re.findall(r"(SELECT\s+.*?;)", text, re.IGNORECASE | re.DOTALL)
        return matches

    def reset_conversation(self):
        import uuid
        return str(uuid.uuid4())

    def get_database_summary(self) -> Dict:
        return {
            "type": self.dialect,
            "tables": self.table_names,
            "table_count": len(self.table_names),
            "domain": self.config.get("domain", {}),
        }


def create_agent_instance(config_path: str = None):
    from tracx_engine.templates import configure as configure_templates
    configure_templates(config_path)

    db_agent = DatabaseAgent(config_path)

    # Wrap with QueryEngine for template-based fast path
    org_id = db_agent.config.get("query_variables", {}).get("org_id")
    if org_id is not None:
        from tracx_engine.engine import QueryEngine
        return QueryEngine(db_agent, org_id=int(org_id))

    # No org_id configured — use raw agent (no template support)
    return db_agent
