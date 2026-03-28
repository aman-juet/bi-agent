# BI Agent

BI Agent is a conversational analytics app for the Instacart market basket dataset. It uses an LLM-powered LangGraph workflow to classify a user request, generate DuckDB SQL, execute the query, and return a natural-language answer with optional chart metadata for the UI.

The repository includes:

- A FastAPI backend in `server.py`
- A Streamlit chat UI in `app.py`
- A lightweight static React UI in `frontend/index.html`
- DuckDB ingestion and schema-caching utilities
- Prompt files, typed response schemas, and test scripts for the agent flow

## What It Does

Given a question like:

- "What are the top 10 most reordered products?"
- "Which department has the highest reorder rate?"
- "Show order volume by day of week as a chart"

the agent will:

1. Run a guardrail step to detect `data_query`, `chit_chat`, or `out_of_scope`
2. Classify whether the question is a follow-up, which tables are needed, and whether a plot is useful
3. Retrieve schema metadata from a local cache
4. Generate SQL for DuckDB
5. Execute the query with retry-on-error logic
6. Produce a concise answer and chart configuration for the frontend

## Repo Structure

```text
.
|-- agent/              # LangGraph nodes, state, tools, graph wiring
|-- data/raw/           # Source Instacart CSV files
|-- db/                 # DuckDB database and ingest script
|-- frontend/           # Static React frontend served by FastAPI
|-- prompts/            # YAML prompts used by each agent step
|-- schemas/            # Pydantic structured output models
|-- testing_modules/    # Script-style test and debugging helpers
|-- utils/              # LLM client, schema cache builder, prompt loader
|-- app.py              # Streamlit UI
|-- server.py           # FastAPI API server
|-- config.py           # Paths, models, env config
|-- requirements.txt
```

## Architecture

The main workflow is defined in `agent/graph.py`:

- `guardrail_node`: filters chit-chat and out-of-scope questions early
- `classifier_node`: identifies tables/views, follow-up status, and plot intent
- `sql_generator_node`: generates SQL and retries when execution fails
- `response_node`: turns query results into a user-facing answer and plot config

The graph uses `MemorySaver` checkpointing, keyed by `thread_id`, so multi-turn conversations can preserve context.

## Data Model

The app is built around the Instacart dataset and creates two important views:

- `order_products`: unions prior and train order-product rows
- `product_full`: enriches products with aisle and department names

These views are the preferred query surfaces for generated SQL.

## Prerequisites

- Python 3.11 recommended
- An OpenAI API key
- Instacart CSV files placed in `data/raw/`

Expected CSV files:

- `orders.csv`
- `order_products__prior.csv`
- `order_products__train.csv`
- `products.csv`
- `aisles.csv`
- `departments.csv`

Dataset source referenced by the repo:

- Kaggle: Instacart Market Basket Analysis

## Setup

### 1. Create and activate a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Create `.env`

Create a `.env` file in the repo root:

```env
OPENAI_API_KEY=sk-...
LANGCHAIN_API_KEY=
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=bi-agent
```

Only `OPENAI_API_KEY` is required. If it is missing, `config.py` raises an error at startup.

## Prepare the Database

### 1. Ingest the CSV data into DuckDB

```powershell
python db\ingest.py
```

To force a rebuild:

```powershell
python db\ingest.py --force
```

This creates or refreshes `db/instacart.db`, validates key column types, and runs a few data-quality checks.

### 2. Build the schema cache

```powershell
python -c "from utils.schema_builder import build_schema_cache; build_schema_cache(force=True)"
```

This generates `db/schema_cache.json`, which the agent uses to retrieve table and view metadata before generating SQL.

## Running the App

### Option A: Recommended development flow

Start the FastAPI backend:

```powershell
python -m uvicorn server:app --reload --port 8000
```

In a second terminal, start the Streamlit frontend:

```powershell
streamlit run app.py
```

Then open:

- Streamlit UI: `http://localhost:8501`
- API docs: `http://127.0.0.1:8000/docs`
- API health check: `http://127.0.0.1:8000/health`

### Option B: Static frontend only

If you just want the static React page served by FastAPI, run the API server and open:

```text
http://127.0.0.1:8000/
```

## API

### `GET /health`

Returns:

```json
{"status":"ok"}
```

### `POST /chat`

Request body:

```json
{
  "query": "What are the top 5 most reordered products?",
  "thread_id": "your-session-id"
}
```

Response shape:

```json
{
  "thread_id": "your-session-id",
  "intent": "data_query",
  "response": "Natural language answer",
  "sql": "SELECT ...",
  "result_data": [],
  "result_columns": [],
  "plot_config": {},
  "retry_count": 0,
  "error": ""
}
```

## Testing and Debugging

The repo includes script-style test modules under `testing_modules/` for checking individual agent steps and graph behavior, for example:

```powershell
python testing_modules\test_graph.py
python testing_modules\test_guardrail.py
python testing_modules\test_response.py
```

These are useful for local debugging, but they are not yet organized as a formal `pytest` suite.

## Notes and Caveats

- `config.py` currently hardcodes `OPENAI_MODEL = "gpt-4.1"` and `OPENAI_MINI_MODEL = "gpt-4o-mini"`.
- Query execution is read-only against DuckDB.
- `MAX_RETRIES` is set to `2`, so SQL generation gets up to 3 execution attempts total.
- Result sizes are intended to stay manageable; non-aggregate queries are guided to limit output.
- Some older testing scripts appear to lag behind the current state shape, so treat them as developer utilities rather than strict regression tests.

## Suggested Questions

- What are the top 10 most reordered products?
- Which department has the highest reorder rate?
- Show order volume by day of week as a chart
- What is the average basket size per order?
- Which aisle has the most unique products?
- How many unique users ordered on weekends?

## Future Improvements

- Convert `testing_modules/` into automated `pytest` coverage
- Add startup scripts for ingest + schema cache bootstrap
- Add authentication and narrower CORS settings for production
- Containerize the backend and frontend for simpler setup
- Add evaluation datasets for SQL correctness and response quality
