import os
import re
import warnings
import yaml
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langgraph.checkpoint.memory import MemorySaver
from typing import Dict, Any

# Load environment variables
load_dotenv()


class DatabaseAgent:
    def __init__(self, config_path: str = None):
        """Initialize the database agent with config-driven settings"""

        # Load domain configuration
        self.config = self._load_config(config_path)

        # Initialize Claude model from config
        llm_config = self.config.get("llm", {})
        self.llm = ChatAnthropic(
            model=llm_config.get("model", "claude-sonnet-4-20250514"),
            temperature=llm_config.get("temperature", 0.1),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            max_tokens=llm_config.get("max_tokens", 4096),
        )

        # Setup database connection using SQLDatabase
        self.setup_database()

        # Setup memory for conversation history
        self.checkpointer = MemorySaver()

        # Create SQL toolkit and tools
        self.setup_tools()

        # Create agent
        self.agent = self._create_agent()

    def _load_config(self, config_path: str = None) -> dict:
        """Load domain configuration from YAML file"""
        path = config_path or os.getenv("AGENT_CONFIG", "config.yaml")
        try:
            with open(path, "r") as f:
                config = yaml.safe_load(f)
            print(f"Loaded config from: {path}")
            return config
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Config file not found: {path}. "
                f"Create a config.yaml or set AGENT_CONFIG env var."
            )

    def setup_database(self):
        """Setup SQLDatabase connection based on environment variables"""
        # Suppress SQLAlchemy warning about circular FK between drivers <-> equipment
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

            # Apply table whitelist from config (if specified)
            include_tables = self.config.get("database", {}).get("include_tables")
            if include_tables:
                self.db = SQLDatabase.from_uri(db_uri, include_tables=include_tables)
            else:
                self.db = SQLDatabase.from_uri(db_uri)

            # Get database info — only table names (lazy schema loading)
            self.dialect = self.db.dialect
            self.table_names = self.db.get_usable_table_names()

            print(f"Connected to {db_type} database")
            print(f"Available tables: {', '.join(self.table_names)}")

        except Exception as e:
            print(f"Database connection error: {e}")
            raise

    def setup_tools(self):
        """Create SQLDatabase toolkit and custom tools"""

        # Create SQLDatabase toolkit
        self.sql_toolkit = SQLDatabaseToolkit(db=self.db, llm=self.llm)

        # Get base tools from toolkit
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
                else:
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
            if not query.strip().upper().startswith("SELECT"):
                return "Only SELECT queries are allowed for safety. Please rephrase your request to use a SELECT query."

            try:
                result = self.db.run(query)
                return result
            except Exception as e:
                return f"Error executing query: {str(e)}"

        @tool
        def check_query_safety(query: str) -> str:
            """
            Check if a SQL query is safe to execute.
            Returns a safety assessment.
            """
            query_upper = query.strip().upper()

            dangerous_keywords = [
                "DROP",
                "DELETE",
                "UPDATE",
                "INSERT",
                "ALTER",
                "CREATE",
                "TRUNCATE",
            ]

            for keyword in dangerous_keywords:
                if keyword in query_upper:
                    return f"UNSAFE: Query contains {keyword} operation. Only SELECT queries are allowed."

            if not query_upper.startswith("SELECT"):
                return "UNSAFE: Query does not start with SELECT. Only SELECT queries are allowed."

            return "SAFE: Query appears to be a read-only SELECT statement."

        # Combine toolkit tools with custom tools
        tool_names = [tool.name for tool in base_tools]

        custom_tools = [
            get_database_info,
            get_table_schema,
            execute_sql_query,
            check_query_safety,
        ]

        self.tools = base_tools + [t for t in custom_tools if t.name not in tool_names]

    def _create_agent(self):
        """Create the LangChain agent using the config-driven system prompt"""

        domain_name = self.config.get("domain", {}).get("name", "AI Agent")
        restriction_msg = self.config.get(
            "domain_restriction_message",
            f"I can only help with questions about your {domain_name} data.",
        )

        # Build filter instructions from query_variables
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

        system_prompt = self.config["system_prompt"].format(
            table_names=", ".join(self.table_names),
            domain_name=domain_name,
            domain_restriction_message=restriction_msg,
            query_filter_instructions=filter_instructions,
            **{k: str(v) for k, v in query_vars.items()},
        )

        agent = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=system_prompt,
            checkpointer=self.checkpointer,
        )

        return agent

    def query(self, user_input: str, thread_id: str = "default") -> Dict[str, Any]:
        """Process a user query and return the response, with automatic retry on SQL errors"""
        max_retries = 2

        try:
            enhanced_input = user_input

            structure_keywords = [
                "table",
                "schema",
                "structure",
                "column",
                "field",
                "database",
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

            output = response["messages"][-1].content

            # Retry logic: if the agent hit a SQL error, send it back for self-correction
            for _ in range(max_retries):
                if "Error executing query" not in output:
                    break
                retry_msg = f"The previous query failed with: {output}\nPlease fix the SQL and try again."
                response = self.agent.invoke(
                    {"messages": [{"role": "user", "content": retry_msg}]},
                    config=config,
                )
                output = response["messages"][-1].content

            sql_queries = self.extract_sql_queries(output)

            return {
                "success": True,
                "response": output,
                "sql_queries": sql_queries,
            }
        except Exception as e:
            return {
                "success": False,
                "response": f"I encountered an error: {str(e)}. Please try rephrasing your question.",
                "error": str(e),
            }

    def extract_sql_queries(self, text: str) -> list:
        """Extract SQL queries from the response text"""
        sql_pattern = r"(SELECT\s+.*?;)"
        matches = re.findall(sql_pattern, text, re.IGNORECASE | re.DOTALL)
        return matches

    def reset_conversation(self):
        """Reset the conversation memory by switching to a new thread"""
        import uuid

        return str(uuid.uuid4())

    def get_database_summary(self) -> Dict:
        """Get a summary of the database for the UI"""
        return {
            "type": self.dialect,
            "tables": self.table_names,
            "table_count": len(self.table_names),
            "domain": self.config.get("domain", {}),
        }


# Factory function — called once at module level in app.py
def create_agent_instance(config_path: str = None):
    """Create a new DatabaseAgent instance"""
    return DatabaseAgent(config_path)
