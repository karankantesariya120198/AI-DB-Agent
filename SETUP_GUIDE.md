# AI Agent - Setup Guide

## Prerequisites

- **Python 3.12+** installed ([python.org/downloads](https://www.python.org/downloads/))
- **Git** installed
- An **Anthropic API key** (get one at [console.anthropic.com](https://console.anthropic.com/))
- A database — one of:
  - **SQLite** (default, no extra setup needed)
  - **MySQL** (requires a running MySQL server)
  - **PostgreSQL** (requires a running PostgreSQL server)

---

## 1. Clone the Repository

```bash
git clone <your-repo-url>
cd AI-Agent
```

---

## 2. Create a Virtual Environment

```bash
python -m venv venv
```

Activate it:

| OS      | Command                    |
|---------|----------------------------|
| Windows | `venv\Scripts\activate`    |
| macOS / Linux | `source venv/bin/activate` |

You should see `(venv)` in your terminal prompt.

---

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs LangChain, LangGraph, Flask, database connectors, pandas, and other utilities.

---

## 4. Configure Environment Variables

Create a `.env` file in the project root (or copy and edit the example below):

```env
# Required — your Anthropic API key
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx

# Domain config file (default: config.yaml)
AGENT_CONFIG=config.yaml

# Database type: sqlite, mysql, or postgresql
DB_TYPE=sqlite

# --- SQLite (default) ---
DB_PATH=sample_database.db

# --- MySQL (uncomment and fill if DB_TYPE=mysql) ---
# DB_USER=root
# DB_PASSWORD=your_password
# DB_HOST=localhost
# DB_PORT=3306
# DB_NAME=your_database

# --- PostgreSQL (uncomment and fill if DB_TYPE=postgresql) ---
# DB_USER=postgres
# DB_PASSWORD=your_password
# DB_HOST=localhost
# DB_PORT=5432
# DB_NAME=your_database
```

### Configuration Details

| Variable           | Required | Description                                      |
|--------------------|----------|--------------------------------------------------|
| `ANTHROPIC_API_KEY`| Yes      | Your Claude API key from Anthropic                |
| `AGENT_CONFIG`     | No       | Path to domain config YAML (default: `config.yaml`) |
| `DB_TYPE`          | Yes      | Database engine: `sqlite`, `mysql`, or `postgresql` |
| `DB_PATH`          | SQLite   | Path to the SQLite file                           |
| `DB_USER`          | MySQL/PG | Database username                                 |
| `DB_PASSWORD`      | MySQL/PG | Database password                                 |
| `DB_HOST`          | MySQL/PG | Database host (default: `localhost`)               |
| `DB_PORT`          | MySQL/PG | Database port (`3306` for MySQL, `5432` for PG)   |
| `DB_NAME`          | MySQL/PG | Database name                                     |

---

## 5. Domain Configuration

The agent's behavior is controlled by a YAML config file (`config.yaml`). This file defines:

- **UI text** — app title, icon, description, input placeholder
- **LLM settings** — model, temperature, max tokens
- **Database restrictions** — optional table whitelist
- **System prompt** — the instructions given to the AI agent
- **Domain restriction message** — shown when users ask off-topic questions

### Switching Domains

To use the agent for a different domain:

1. Copy an existing config: `cp domains/tms.yaml domains/my_domain.yaml`
2. Edit the domain fields, system prompt, and restriction message
3. Set `AGENT_CONFIG=domains/my_domain.yaml` in `.env`
4. Update database credentials in `.env`
5. Restart: `python app.py`

Example configs are provided in the `domains/` folder:
- `domains/tms.yaml` — Transportation Management System
- `domains/ecommerce.yaml` — E-Commerce store

---

## 6. Set Up a Sample Database (Optional)

If you don't have an existing database, generate a sample SQLite database:

```bash
python setup_db.py
```

This creates `sample_database.db` with sample tables (categories, customers, products, orders, order_items) and populates them with test data.

> **Note:** The default config is set up for TMS tables. The sample database is useful for verifying the setup works. Point `DB_TYPE` and connection details to your real database for production use.

---

## 7. Run the Application

```bash
python app.py
```

The Flask server will start on `http://localhost:5000`. Open it in your browser.

---

## 8. Using the App

1. The agent connects to your database automatically on first load.
2. Type a question in the chat input, e.g.:
   - *"How many loads are there?"*
   - *"Show me first 5 loads"*
   - *"What drivers have the most settlements?"*
3. Responses stream in real-time. Tabular results are rendered as HTML tables.
4. Click **Clear** (top-right) to reset the conversation.

### Domain Restriction

The agent only answers questions within the configured domain. Off-topic questions are politely declined. This behavior is controlled by the `system_prompt` and `domain_restriction_message` in your config YAML.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError` | Make sure the virtual environment is activated and you ran `pip install -r requirements.txt` |
| `Config file not found` | Ensure `AGENT_CONFIG` in `.env` points to a valid YAML file (default: `config.yaml`) |
| Database connection error | Verify `.env` values. For MySQL/PG, ensure the server is running and credentials are correct |
| `ANTHROPIC_API_KEY` error | Check that your API key is valid and has available credits |
| Port 5000 already in use | Change the port in `app.py` (`app.run(port=5001)`) or set `FLASK_RUN_PORT` |

---

## Project Structure

```
AI-Agent/
  config.yaml           # Active domain configuration
  agent.py              # Core agent logic (LangChain + Claude)
  app.py                # Flask web server
  setup_db.py           # Sample SQLite database generator
  requirements.txt      # Python dependencies
  domains/              # Example domain configs
    tms.yaml
    ecommerce.yaml
  templates/
    index.html          # Chat UI page (uses Jinja2 variables from config)
  static/
    css/style.css       # Styling
    js/app.js           # Chat logic & SSE streaming
  .env                  # Environment variables (not committed)
```
