# BI Agent

A conversational analytics system built on the Instacart grocery dataset. Ask questions in plain English and get SQL-backed answers with charts, data tables, and natural language summaries.

Built with LangGraph, FastAPI, DuckDB, and GPT-4o. The frontend is a single React page served directly by FastAPI — no Node, no npm, no build step.

---

## What It Does

Ask questions like:

- "What are the top 10 most reordered products?"
- "Which department has the highest reorder rate?"
- "Show order volume by day of week as a chart"
- "Now filter that to only the produce department"

The agent will:

1. Classify the intent — chit-chat, out-of-scope, or data query
2. Identify which tables are needed and whether a chart is useful
3. Fetch schema metadata for only those tables
4. Generate DuckDB SQL and execute it
5. Retry with error feedback if execution fails (up to 2 retries)
6. Return a natural language summary, chart config, raw data, and the SQL

---

## Architecture

```
Browser (React)
    ↓  POST /chat
FastAPI (server.py)
    ↓  app_graph.invoke()
LangGraph Agent
    ├── guardrail_node     classify intent, handle chit-chat and off-topic
    ├── classifier_node    identify tables, plot type, follow-up detection
    ├── sql_generator_node generate SQL, execute via DuckDB, retry on failure
    └── response_node      natural language summary + plot config
    ↓
DuckDB (33.8M rows) + OpenAI GPT-4o
```

The graph uses `MemorySaver` checkpointing keyed by `thread_id` so follow-up questions preserve context across turns.

See `docs/ARCHITECTURE.md` for a full breakdown of every node, tool, and design decision.

---

## Repo Structure

```
.
├── agent/              LangGraph nodes, state, tools, graph wiring
├── db/                 DuckDB ingest script
├── docs/               Architecture and data documentation, HLD diagram
├── frontend/           Static React UI served by FastAPI
├── prompts/            YAML prompt files for each agent node
├── schemas/            Pydantic structured output models
├── testing_modules/    Per-node test scripts
├── utils/              LLM client, schema builder, prompt loader, tracer
├── server.py           FastAPI backend
├── config.py           Paths, model names, env config
└── requirements.txt
```

---

## Prerequisites

- Python 3.11+
- OpenAI API key
- Instacart CSV files in `data/raw/`

Download the dataset from Kaggle:
https://www.kaggle.com/datasets/psparks/instacart-market-basket-analysis

Expected files:

```
data/raw/orders.csv
data/raw/order_products__prior.csv
data/raw/order_products__train.csv
data/raw/products.csv
data/raw/aisles.csv
data/raw/departments.csv
```

---

## Setup

### 1. Install dependencies

```powershell
pip install -r requirements.txt
```

### 2. Create `.env`

```env
OPENAI_API_KEY=sk-...

# Optional: LangSmith observability
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=bi-agent
```

Only `OPENAI_API_KEY` is required. The app raises a clear error at startup if it is missing.

### 3. Ingest the data

```powershell
python db\ingest.py
```

This loads all 6 CSVs into DuckDB, creates analytical views, validates column types, and runs data quality checks. Use `--force` to rebuild from scratch:

```powershell
python db\ingest.py --force
```

### 4. Build the schema cache

```powershell
python -c "from utils.schema_builder import build_schema_cache; build_schema_cache(force=True)"
```

This generates `db/schema_cache.json` — LLM-generated table and column descriptions used by the agent at query time. Takes about 60 seconds on first run. Subsequent runs load from cache instantly.

---

## Running the App

Start the FastAPI server:

```powershell
python -m uvicorn server:app --reload --port 8000
```

Open the browser at:

```
http://127.0.0.1:8000
```

The React frontend is served directly by FastAPI. No second terminal needed.

Useful endpoints:

```
GET  /health   server health check
POST /chat     main agent endpoint
GET  /docs     auto-generated API docs
```

---

## API

### `POST /chat`

Request:

```json
{
  "query": "What are the top 5 most reordered products?",
  "thread_id": "your-session-uuid"
}
```

Response:

```json
{
  "thread_id": "your-session-uuid",
  "intent": "data_query",
  "response": "Banana is the most reordered product with 415,166 reorders...",
  "sql": "SELECT pf.product_name, COUNT(*) AS total_reorders FROM...",
  "result_data": [...],
  "result_columns": ["product_name", "total_reorders"],
  "plot_config": {
    "chart_type": "bar",
    "x_column": "product_name",
    "y_column": "total_reorders",
    "title": "Top 5 Most Reordered Products"
  },
  "retry_count": 0,
  "error": ""
}
```

---

## Data Model

The dataset has 6 raw tables totalling 33.8 million rows. Two views are created at ingest time and are the preferred query surfaces:

**`order_products`** — unions prior and train order-product rows into a single 33.8M row table. The prior/train split is a machine learning artifact irrelevant for BI.

**`product_full`** — joins products, aisles, and departments into a flat table. Products labeled `aisle='missing'` (Instacart's uncategorized catch-all) are excluded at the view level.

| Table | Rows |
|---|---|
| orders | 3,421,083 |
| order_products_prior | 32,434,489 |
| order_products_train | 1,384,617 |
| products | 49,688 |
| aisles | 134 |
| departments | 21 |
| order_products (view) | 33,819,106 |
| product_full (view) | 48,430 |

---

## Security

SQL injection is prevented at three independent layers:

1. The guardrail prompt explicitly blocks destructive SQL commands and prompt injection attempts
2. The query executor runs a regex check against a forbidden keyword list before execution (DROP, DELETE, TRUNCATE, INSERT, UPDATE, ALTER, etc.)
3. The DuckDB connection is opened read-only — write operations are physically rejected

---

## Observability

If `LANGCHAIN_API_KEY` is set, all LangGraph node traces are sent to LangSmith automatically. Each query shows the full node-by-node trace with inputs, outputs, token usage, and latency at `smith.langchain.com`.

Local console tracing is also enabled via `utils/tracer.py` — every node entry, table selection, SQL attempt, and response summary is logged to stdout.

---

## Testing

Per-node test scripts are in `testing_modules/`:

```powershell
python testing_modules\test_graph.py
python testing_modules\test_guardrail.py
python testing_modules\test_classifier.py
python testing_modules\test_sql_generator.py
python testing_modules\test_response.py
```

These are developer utilities for checking individual agent steps, not a formal pytest suite.

---

## Known Limitations

- The dataset has no absolute timestamps. Questions about monthly or yearly trends cannot be answered accurately. The system will attempt a related temporal analysis but cannot reconstruct calendar dates.
- Semantically wrong but executable SQL is not detected. The retry loop catches execution failures only.
- `MemorySaver` is in-memory — conversation history is lost on server restart.
- Result sets are capped at 1000 rows for non-aggregate queries.

---

## Documentation

- `docs/ARCHITECTURE.md` — full node-by-node architecture, design decisions, tool reference
- `docs/DATA_AND_METADATA.md` — data ingestion pipeline and schema cache generation in detail
- `docs/bi_agent_hld.drawio` — draw.io HLD diagram

---

## Suggested Questions

- What are the top 10 most reordered products?
- Which department has the highest reorder rate?
- Show order volume by day of week as a chart
- What is the average basket size per order?
- Which aisle has the most unique products?
- Which aisles have the highest reorder rate and does that correlate with how early products are added to the cart?
- How many unique users ordered on weekends?
- Show me the top 5 departments by order volume, then filter to only those with reorder rate above 0.65
