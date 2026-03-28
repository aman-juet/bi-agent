# Data Ingestion & Metadata Creation

## Overview

Before the BI Agent can answer any question, two one-time setup steps must run:

1. **Data Ingestion** — loads 6 raw CSV files into a local DuckDB database, casts types correctly, creates analytical views, and validates data quality.
2. **Metadata Generation** — uses an LLM to generate human-readable descriptions of every table and column, stores them in a JSON cache that the agent reads at query time.

Both steps are idempotent — they skip if the output already exists unless `--force` is passed.

---

## Step 1: Data Ingestion (`db/ingest.py`)

### What it does

```
6 CSV files (data/raw/)
        ↓
DuckDB (db/instacart.db)
        ↓
2 analytical views
        ↓
7 data quality checks
        ↓
3-table join smoke test
```

### Why DuckDB

The dataset has ~33.8 million rows across two product tables. Standard row-oriented databases like SQLite perform full table scans for every aggregation query — too slow for a live demo. DuckDB is a columnar, in-process analytical engine that handles 33M rows in milliseconds without a separate server process. It also supports direct CSV ingestion, views, and full SQL including window functions and CTEs.

### Raw Table Loading

Each CSV is loaded as a native DuckDB table using `read_csv_auto`. The `orders` table requires special handling because `days_since_prior_order` is stored as an empty string for first orders rather than a true NULL:

```sql
CREATE OR REPLACE TABLE orders AS
SELECT
    CAST(order_id AS INTEGER)                                    AS order_id,
    CAST(user_id AS INTEGER)                                     AS user_id,
    eval_set,
    CAST(order_number AS INTEGER)                                AS order_number,
    CAST(order_dow AS INTEGER)                                   AS order_dow,
    CAST(order_hour_of_day AS INTEGER)                           AS order_hour_of_day,
    TRY_CAST(NULLIF(TRIM(days_since_prior_order), '') AS DOUBLE) AS days_since_prior_order
FROM read_csv_auto('orders.csv', all_varchar=true)
```

`NULLIF(TRIM(...), '')` converts empty strings to NULL before casting to DOUBLE. `TRY_CAST` ensures rows that fail the cast return NULL rather than raising an error. All other integer columns are explicitly cast from varchar to avoid DuckDB inferring them as BIGINT when they should be INTEGER.

All other tables use `read_csv_auto` with `nullstr='NA'` which handles standard missing value representation.

### Analytical Views

Two views are created on top of the raw tables. These are what the LLM queries — never the raw tables directly.

**`order_products`** — unions prior and train order-product tables:

```sql
CREATE OR REPLACE VIEW order_products AS
SELECT order_id, product_id, add_to_cart_order, reordered, 'prior' AS eval_set
FROM order_products_prior
UNION ALL
SELECT order_id, product_id, add_to_cart_order, reordered, 'train' AS eval_set
FROM order_products_train
```

The Instacart dataset splits product data across two files — `order_products__prior.csv` (historical orders) and `order_products__train.csv` (most recent orders used for ML training). This split is a machine learning artifact, not a business distinction. For a BI tool, both sets contain valid purchase history and should be treated as one unified table. The UNION preserves an `eval_set` column for transparency.

**`product_full`** — pre-joins products with aisles and departments:

```sql
CREATE OR REPLACE VIEW product_full AS
SELECT
    p.product_id,
    p.product_name,
    p.aisle_id,
    a.aisle,
    p.department_id,
    d.department
FROM products p
JOIN aisles a ON p.aisle_id = a.aisle_id
JOIN departments d ON p.department_id = d.department_id
WHERE a.aisle != 'missing'
AND d.department != 'missing'
```

This eliminates the need for the LLM to write 3-table joins just to get a product name with its category. It also filters out `aisle='missing'` (aisle_id=100) — Instacart's catch-all for 1,258 uncategorized products. Filtering at the view layer means the LLM never sees this category and cannot accidentally surface it in results.

### Data Quality Checks

Seven automated checks run after ingestion. Any failure logs a WARNING rather than aborting — this allows inspection without halting setup:

| Check                       | What it validates                                                  |
| --------------------------- | ------------------------------------------------------------------ |
| reordered flag values       | Only 0 or 1 in `order_products_prior.reordered`                    |
| order_dow range             | Values between 0 and 6 only                                        |
| order_hour_of_day range     | Values between 0 and 23 only                                       |
| No orphan product_ids       | Every `product_id` in order_products_prior exists in products      |
| No orphan order_ids         | Every `order_id` in order_products_prior exists in orders          |
| NULL days_since_prior_order | NULLs only exist where `order_number = 1`                          |
| No missing aisle/department | product_full contains no rows with aisle or department = 'missing' |

### Idempotency

On startup, `ingest()` checks whether all required tables already exist in DuckDB:

```python
tables = con.execute("SHOW TABLES").fetchdf()["name"].tolist()
return all(t in tables for t in CSV_FILES.keys())
```

If all tables are present, ingest is skipped. This means starting the server never re-processes 250MB of CSV. To force a full re-ingest:

```bash
python db/ingest.py --force
```

### Final Row Counts

| Table / View          | Rows       |
| --------------------- | ---------- |
| orders                | 3,421,083  |
| order_products_prior  | 32,434,489 |
| order_products_train  | 1,384,617  |
| products              | 49,688     |
| aisles                | 134        |
| departments           | 21         |
| order_products (view) | 33,819,106 |
| product_full (view)   | 48,430     |

---

## Step 2: Metadata Generation (`utils/schema_builder.py`)

### What it does

```
DuckDB (all tables + views)
        ↓
For each table:
  - Row count (full scan)
  - Column stats (type, unique count, null%, sample values)
  - Sample rows (5 rows)
        ↓
LLM: table description (1 sentence per table)
LLM: column descriptions (1 sentence per column)
        ↓
schema_cache.json
        ↓
schema_string (injected into SQL generator prompt at query time)
```

### Why generate metadata with an LLM

A standard schema string would look like this:

```
orders: order_id INTEGER, user_id INTEGER, eval_set VARCHAR, ...
```

This is technically accurate but analytically useless. A SQL-generating LLM needs to understand what each column _means_ — not just its type. With LLM-generated descriptions, the schema string looks like this:

```
TABLE: orders (3,421,083 rows)
  Description: This table records individual customer orders, capturing the user,
               order sequence, timing, and interval since the previous order.
  Columns:
    order_id (INTEGER)  [unique: 3421083, null%: 0.0]
      — Unique identifier for each order.
    days_since_prior_order (DOUBLE)  [unique: 31, null%: 6.0]
      — Number of days since the user's previous order; null for the first order.
```

This is what gets injected into the SQL generator's system prompt. The richer the context, the fewer SQL errors and retries.

### Column Stats Collection

For each table, three DuckDB queries run:

1. `SELECT * FROM {table} LIMIT 500` — fetches a sample for null detection and sample values
2. `DESCRIBE {table}` — gets accurate DuckDB column types (not pandas-inferred types)
3. `SELECT COUNT(DISTINCT col) FROM {table}` for each column — gets accurate cardinality from the full table, not the 500-row sample

Why not just use the 500-row sample for everything? Cardinality from 500 rows is misleading. `order_id` would show ~500 unique values instead of 3.4 million. The LLM would incorrectly infer it's a low-cardinality categorical column rather than a primary key. Running `COUNT(DISTINCT)` on the full table via DuckDB's columnar engine takes under 2 seconds even on 33M rows.

### LLM Table Description

For each table, a single LLM call generates a one-sentence functional description:

```python
prompt = f"""
You are a data analyst. Given the table name, its columns, and sample data,
write a single concise sentence describing what this table contains and its purpose.

Table: {table_name}
Columns: {cols_str}
Sample rows: {sample_str}

Return only the description sentence, nothing else.
"""
```

### LLM Column Descriptions

A second LLM call generates one-sentence descriptions for every column in the table:

```python
prompt = f"""
Given a table and its columns, write a short functional description for each column.
Descriptions should help someone write SQL queries against this table.

Return a JSON object mapping column_name to its description.
"""
```

The response is parsed as JSON and merged into the schema block. If parsing fails, the column description is omitted gracefully — the schema block still renders with type and sample value information.

### Schema Cache

Everything is written to `db/schema_cache.json`:

```json
{
  "tables": {
    "orders": {
      "kind": "table",
      "row_count": 3421083,
      "col_stats": [...],
      "sample_rows": [...],
      "table_description": "This table records individual customer orders...",
      "column_descriptions": {
        "order_id": "Unique identifier for each order.",
        "days_since_prior_order": "Number of days since the user's previous order..."
      },
      "schema_block": "TABLE: orders (3,421,083 rows)\n  Description: ..."
    }
  },
  "schema_string": "=== INSTACART DATABASE SCHEMA ===\n\n..."
}
```

The `schema_string` field is the concatenation of all `schema_block` entries plus `USAGE_NOTES` — query rules, join keys, and special handling instructions. This single string is what gets injected into the SQL generator prompt.

### Idempotency

On first run, the cache file does not exist — full generation runs (8 LLM calls, ~60 seconds). On every subsequent run, the cache file is read from disk instantly. To force regeneration:

```bash
python -c "from utils.schema_builder import build_schema_cache; build_schema_cache(force=True)"
```

Regeneration is only needed if the underlying data changes (re-ingest with `--force`) or if you want to improve the LLM-generated descriptions.

### Runtime Usage

At query time, `metadata_retriever` (a LangChain tool) reads from the cache for only the tables needed by the current query:

```python
@tool
def metadata_retriever(table_names: list[str]) -> str:
    cache = json.loads(SCHEMA_CACHE_PATH.read_text())
    blocks = [cache["tables"][name]["schema_block"] for name in table_names]
    return "\n\n".join(blocks)
```

This means a query about products only injects product-related schema context — not the full 8-table schema. Smaller context = fewer tokens = faster, cheaper, more accurate SQL generation.

---

## Running Both Steps

```bash
# Step 1: Data ingestion
python db/ingest.py

# Step 2: Metadata generation
python -c "from utils.schema_builder import build_schema_cache; build_schema_cache(force=True)"

# Start the server (both steps are skipped automatically if already done)
python -m uvicorn server:app --reload --port 8000
```

Both steps are one-time setup. Once `instacart.db` and `schema_cache.json` exist, the server starts in under 5 seconds.
