# BI Agent Architecture

## System Overview

The BI Agent is a conversational analytics system built on top of the Instacart grocery dataset. It accepts natural language questions, generates and executes SQL against a local DuckDB database, and returns results as natural language summaries with charts and data tables.

The system is structured as a **FastAPI backend** serving a **React frontend**, with all analytical intelligence handled by a **LangGraph agent** that orchestrates multiple LLM calls, tools, and state transitions per query.

```
User (Browser)
    ↓  ↑
React Frontend (index.html — CDN, no Node)
    ↓  ↑  POST /chat
FastAPI Backend (server.py)
    ↓  ↑  app_graph.invoke()
LangGraph Agent (agent/graph.py)
    ↓  ↑
4 Nodes + 2 Tools + MemorySaver
    ↓  ↑
DuckDB (33.8M rows) + OpenAI (gpt-4o)
```

---

## Project Structure

```
bi_agent/
├── agent/
│   ├── graph.py          # LangGraph graph assembly, edges, MemorySaver
│   ├── nodes.py          # All node functions
│   ├── state.py          # AgentState TypedDict
│   └── tools.py          # metadata_retriever + query_executor tools
├── db/
│   ├── ingest.py         # One-time CSV → DuckDB ingestion
│   ├── instacart.db      # DuckDB database (not in git)
│   └── schema_cache.json # LLM-generated schema metadata (not in git)
├── docs/
│   ├── ARCHITECTURE.md   # This file
│   ├── DATA_AND_METADATA.md
│   └── bi_agent_hld.drawio
├── frontend/
│   └── index.html        # React SPA served by FastAPI
├── prompts/
│   ├── guardrail.yaml
│   ├── classifier.yaml
│   ├── sql_generator.yaml
│   └── response.yaml
├── schemas/
│   └── schemas.py        # Pydantic output models for all nodes
├── utils/
│   ├── llm_client.py     # ChatOpenAI wrapper
│   ├── prompt_loader.py  # YAML prompt loader
│   └── schema_builder.py # DuckDB + LLM schema cache generator
├── config.py             # Paths, model names, env vars
├── server.py             # FastAPI application
└── .env                  # API keys (not in git)
```

---

## LangGraph Agent

### Why LangGraph

The agent has a genuine branching flow — a query can exit early after guardrail, retry after SQL failure, or traverse the full pipeline. LangGraph models this as a directed graph with conditional edges. The `MemorySaver` checkpointer persists conversation state per session thread, enabling follow-up questions without manual history management.

### Agent State

Every node reads from and writes to a single `AgentState` TypedDict that flows through the graph:

```python
class AgentState(TypedDict):
    user_query: str          # current user input
    intent: str              # "chit_chat" | "out_of_scope" | "data_query"
    is_followup: bool        # references prior result?
    plot_needed: bool        # should a chart be rendered?
    plot_type: str           # "bar" | "line" | "pie" | "scatter" | "none"
    table_names: list[str]   # tables needed for this query
    metadata_context: str    # schema blocks for relevant tables
    sql: str                 # generated SQL query
    retry_count: int         # number of SQL retries attempted
    error: str               # last error message if SQL failed
    result_data: list[dict]  # query results as list of row dicts
    result_columns: list[str]# column names for result_data
    plot_config: dict        # {chart_type, x_column, y_column, title}
    response_text: str       # natural language answer for the user
    messages: list[BaseMessage] # full conversation history (persisted by MemorySaver)
```

`result_data` and `result_columns` store results as plain Python types rather than a DataFrame. This is required because `MemorySaver` serializes state to msgpack, which cannot serialize pandas DataFrames. Any node that needs a DataFrame reconstructs it locally from these fields.

### Graph Structure

```
__start__
    ↓
guardrail_node
    ├── chit_chat / out_of_scope ──────────────────→ __end__
    └── data_query
            ↓
        classifier_node
            ↓ (calls metadata_retriever tool)
        sql_generator_node
            ↓ (calls query_executor tool)
            ├── success ──────────────────────────→ response_node → __end__
            └── failure → retry (max 2) ──────────→ response_node → __end__
```

Solid edges are unconditional. The dashed edge from `guardrail_node` is a conditional edge implemented via `route_after_guardrail()`:

```python
def route_after_guardrail(state: AgentState) -> str:
    if state["intent"] in ("chit_chat", "out_of_scope"):
        return END
    return "classifier_node"
```

---

## Node Reference

### `guardrail_node`

**Purpose:** Entry gate for every query. Classifies intent and generates a response for non-data queries.

**Model:** `gpt-4o` with `GuardrailOutput` structured output

**Input state fields:** `user_query`, `messages`

**Output state fields:** `intent`, `response_text`, `messages`

**Intent values:**
- `chit_chat` — greetings, thanks, small talk. Responds warmly and exits the graph.
- `out_of_scope` — recipes, sports, general knowledge. Politely declines and exits.
- `data_query` — anything requiring Instacart data. Passes through to classifier.

**Design decision:** A single node handles both chit-chat and guardrailing rather than having two separate nodes. This saves one LLM call per data query (the chit-chat check would run on every request in the two-node design). The guardrail prompt is designed to be permissive — when in doubt, classify as `data_query`. A false block is worse than a false allow.

**Prompt file:** `prompts/guardrail.yaml`

---

### `classifier_node`

**Purpose:** Analyzes a data query to determine what's needed before SQL can be generated.

**Model:** `gpt-4o` with `ClassifierOutput` structured output

**Input state fields:** `user_query`, `messages`

**Output state fields:** `is_followup`, `plot_needed`, `plot_type`, `table_names`, `metadata_context`

**ClassifierOutput schema:**
```python
class ClassifierOutput(BaseModel):
    is_followup: bool        # references prior result?
    plot_needed: bool        # should a chart be rendered?
    plot_type: Literal["bar", "line", "pie", "scatter", "none"]
    table_names: list[str]   # from allowed list only
```

After receiving the LLM's classification, the node immediately calls the `metadata_retriever` tool with `table_names` to fetch schema context for only the relevant tables. This context is stored in `metadata_context` and passed to `sql_generator_node`.

**Why classify table names here:** Injecting the full 8-table schema into every SQL generation prompt would waste tokens and dilute relevance. By identifying which 1-3 tables are needed upfront, we inject only the relevant schema — smaller context, fewer hallucinations, faster generation.

**Prompt file:** `prompts/classifier.yaml`

---

### `sql_generator_node`

**Purpose:** Generates SQL, executes it against DuckDB, and handles retries on failure.

**Model:** `gpt-4o` (plain text output — SQL is a string, not a structured schema)

**Input state fields:** `user_query`, `metadata_context`, `messages`, `is_followup`, `sql`

**Output state fields:** `sql`, `result_data`, `result_columns`, `error`, `retry_count`

**Retry loop:**

The retry loop runs inside the node as a Python `for` loop — not as a graph edge. This keeps the graph clean while still providing error recovery:

```python
for attempt in range(MAX_RETRIES + 1):
    # generate SQL
    current_sql = llm.invoke(messages).content

    # execute
    exec_result = query_executor.invoke({"sql": current_sql})

    if exec_result["success"]:
        return {..., "result_data": exec_result["data"]}
    else:
        # feed error back to LLM for self-correction
        error_feedback = exec_result["error"]
```

On failure, the error message and the failed SQL are both fed back into the next LLM call as context. This gives the LLM enough information to correct column names, fix join conditions, or adjust aggregations.

**Follow-up handling:** If `is_followup` is True and a prior SQL exists in state, it's injected into the prompt as `{prior_sql}`. This allows the LLM to build on or modify the previous query rather than starting from scratch.

**Markdown fence stripping:** Some LLM responses wrap SQL in triple backticks despite instructions not to. A post-processing step strips these before execution:

```python
if raw_sql.startswith("```"):
    raw_sql = raw_sql.split("```")[1]
    if raw_sql.lower().startswith("sql"):
        raw_sql = raw_sql[3:]
```

**Prompt file:** `prompts/sql_generator.yaml`

---

### `response_node`

**Purpose:** Converts raw query results into a natural language summary and a plot configuration.

**Model:** `gpt-4o` with `ResponseOutput` structured output

**Input state fields:** `user_query`, `sql`, `result_data`, `result_columns`, `plot_needed`, `plot_type`, `error`

**Output state fields:** `response_text`, `plot_config`, `messages`

**ResponseOutput schema:**
```python
class ResponseOutput(BaseModel):
    response_text: str
    plot_config: Optional[PlotConfig]

class PlotConfig(BaseModel):
    chart_type: Literal["bar", "line", "pie", "scatter", "none"]
    x_column: str     # must match an actual column name in result_data
    y_column: str     # must match an actual column name in result_data
    title: str
```

The LLM receives the first 20 rows of results as a formatted string and generates both the summary and the plot config in a single call. The frontend reads `plot_config` directly and passes it to Plotly.js — no chart logic lives in the backend.

**Error handling:** If `state["error"]` is set (all SQL retries failed), `response_node` skips the LLM call entirely and returns a formatted error message directly.

**Prompt file:** `prompts/response.yaml`

---

## Tools

### `metadata_retriever`

```python
@tool
def metadata_retriever(table_names: list[str]) -> str
```

Reads `db/schema_cache.json` and returns the pre-built schema blocks for the requested tables. Returns a concatenated string of schema blocks — one per table — separated by double newlines.

Validates table names against an allowlist before reading. Returns a clear error string if an invalid table is requested or the cache file is missing.

Called by `classifier_node` after classification. Never called by the LLM directly — it's invoked as a Python function call, not as an LLM tool use.

### `query_executor`

```python
@tool
def query_executor(sql: str) -> dict
```

Opens a read-only DuckDB connection, executes the SQL, and returns:

```python
{
    "success": bool,
    "data": list[dict],      # rows as dicts
    "columns": list[str],    # column names
    "row_count": int,
    "error": str             # empty on success
}
```

Results are truncated to `MAX_RESULT_ROWS` (1000) before returning. Read-only connection prevents any accidental writes to the database. Exceptions are caught and returned as `{"success": False, "error": str(e)}` rather than raised — this allows the retry loop to inspect the error and feed it back to the LLM.

---

## Memory and Conversation History

`MemorySaver` is a LangGraph in-memory checkpointer. Each browser session generates a UUID (`thread_id`) on page load. Every graph invocation passes this thread_id in the config:

```python
config = {"configurable": {"thread_id": thread_id}}
app_graph.invoke(initial_state, config=config)
```

LangGraph automatically persists the `messages` field of `AgentState` after each invocation and restores it at the start of the next invocation for the same thread. This means every node has access to full conversation history without the frontend sending it explicitly.

The `messages` list uses LangChain's standard message types (`HumanMessage`, `AIMessage`, `SystemMessage`). The last 6 messages are injected into each node's prompt as `{conversation_history}` — a sliding window that keeps context relevant without growing indefinitely.

**Session lifecycle:** A new thread_id is generated when the user clicks "New Conversation". The old thread's history remains in MemorySaver but is no longer referenced.

---

## Prompt Management

All system prompts are stored as YAML files in `prompts/`. Each file has a `system` key containing the prompt template and a `config` key with model settings:

```yaml
system: |
  You are a ...
  {conversation_history}

config:
  temperature: 0.0
```

`utils/prompt_loader.py` reads these files at runtime:

```python
def load_prompt(name: str) -> dict:
    path = PROMPTS_DIR / f"{name}.yaml"
    return yaml.safe_load(path.read_text())
```

Template variables (`{conversation_history}`, `{metadata_context}`, etc.) are replaced with `.replace()` in each node before the LLM call. This approach was chosen over Jinja2 or LangChain's `PromptTemplate` to keep prompts readable and editable without Python knowledge — important for iterating during a hackathon.

---

## Frontend

The frontend is a single HTML file (`frontend/index.html`) served by FastAPI at `GET /`. It loads React 18 and Plotly.js from Cloudflare CDN — no Node.js, no npm, no build step required.

**Why this approach:** The requirement to run locally on a machine without a Node environment made a CDN-based React app the right call. Babel Standalone compiles JSX in the browser at runtime. In production this would be replaced with a proper build, but for a demo it's fast to iterate and has zero setup dependencies.

**Session management:** A UUID is generated once via `useState` on page load and stored in React state. It's passed with every `/chat` request as `thread_id`. If the user clicks "New Conversation", a new UUID is generated and React state is cleared.

**Chart rendering:** The `Chart` component receives `plot_config`, `result_data`, and `result_columns` as props. It calls `Plotly.newPlot()` imperatively via a `useRef` hook. The chart type, axis columns, and title all come from the backend's `ResponseOutput` — the frontend contains no chart selection logic.

**Communication:** Plain `fetch()` API — no axios or other HTTP libraries. POST to `/chat`, receive JSON, update React state, trigger re-render.

---

## LLM Client

```python
def get_llm() -> ChatOpenAI          # gpt-4o, temperature=0.0
def get_mini_llm() -> ChatOpenAI     # gpt-4o-mini, temperature=0.7
def get_structured_llm(schema)       # gpt-4o with .with_structured_output(schema)
```

`get_structured_llm` wraps `get_llm()` with LangChain's `.with_structured_output()` which uses OpenAI's function calling API to guarantee the response matches the Pydantic schema. This eliminates JSON parsing errors and schema validation failures — the LLM either returns a valid object or raises an exception.

`gpt-4o-mini` is used for the guardrail's chit-chat responses with `temperature=0.7` — casual conversation should feel natural, not robotic. All analytical nodes use `gpt-4o` at `temperature=0.0` for deterministic SQL and structured output.

---

## Observability

LangSmith tracing is enabled via environment variables in `.env`:

```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=bi-agent
```

When set, every graph invocation is automatically traced at `smith.langchain.com`. Each node appears as a step in the trace with its input state, output state, token usage, and latency. No code changes are needed — LangChain instruments all LLM calls automatically when tracing is enabled.

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| DuckDB over SQLite | Columnar engine handles 33M row aggregations in milliseconds. SQLite would time out on analytical queries. |
| Views over raw tables | `order_products` and `product_full` hide join complexity from the LLM. Fewer joins = fewer errors. |
| Single guardrail node | Handles chit-chat and out-of-scope in one LLM call. Two-node design would waste a call on every data query. |
| Retry loop inside node | SQL retry is a deterministic internal loop, not a branching decision. A graph edge would add visual complexity without adding capability. |
| Selective metadata injection | Only inject schema for tables needed by the current query. Full schema injection wastes tokens and dilutes relevance. |
| Plain list[dict] in state | DataFrames can't be serialized by MemorySaver. Storing rows as plain dicts keeps state serializable without losing any information. |
| YAML prompts | Prompts are editable without touching Python. Easier to iterate during a hackathon and easier to explain to non-technical stakeholders. |
| CDN React | No Node.js required. Single HTML file, zero build step, opens in any browser. |
| FastAPI serves frontend | Eliminates CORS issues with file:// protocol. One command runs everything. |
