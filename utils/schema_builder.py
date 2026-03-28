import json
import logging
import sys
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.llm_client import get_llm
from langchain_core.messages import HumanMessage
from config import DB_PATH

SCHEMA_CACHE_PATH = Path(__file__).parent.parent / "db" / "schema_cache.json"

TABLES_AND_VIEWS = {
    "tables": ["orders", "products", "aisles", "departments", "order_products_prior", "order_products_train"],
    "views": ["order_products", "product_full"]
}

USAGE_NOTES = """
--- QUERY RULES ---
1. Always use the order_products VIEW, never query order_products_prior or order_products_train directly.
2. Always use product_full VIEW when you need product name, aisle, or department — avoids manual joins.
3. days_since_prior_order is NULL for a user's first order (order_number = 1). Use COALESCE(days_since_prior_order, 0) or filter with WHERE days_since_prior_order IS NOT NULL depending on context.
4. reordered is an INTEGER flag: 1 = reordered, 0 = first time ordered. Use AVG(reordered) for reorder rate.
5. order_dow mapping: 0=Saturday, 1=Sunday, 2=Monday, 3=Tuesday, 4=Wednesday, 5=Thursday, 6=Friday.
6. add_to_cart_order is the position in which the item was added to the cart (1 = first item).
7. Always LIMIT result sets to 1000 rows for non-aggregate queries.
8. Return only the SQL query — no explanation, no markdown fences, no preamble.
9. The product_full view excludes products with aisle='missing' or department='missing' — these are uncategorized products in the raw dataset.

--- JOIN KEYS ---
orders.order_id            -> order_products.order_id
order_products.product_id  -> product_full.product_id
"""


def _get_connection():
    return duckdb.connect(str(DB_PATH), read_only=True)


def _get_row_count(con, name: str) -> int:
    try:
        return con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    except Exception:
        logger.warning(f"Could not get row count for {name}")
        return -1


def _get_column_stats(con, name: str) -> list[dict]:
    try:
        df = con.execute(f"SELECT * FROM {name} LIMIT 500").fetchdf()
    except Exception:
        logger.warning(f"Could not fetch sample for {name}")
        return []

    try:
        type_info = con.execute(f"DESCRIBE {name}").fetchdf()
        duckdb_types = dict(zip(type_info["column_name"], type_info["column_type"]))
    except Exception:
        duckdb_types = {}

    try:
        count_query = ", ".join([f"COUNT(DISTINCT {col}) AS {col}" for col in df.columns])
        unique_counts = con.execute(f"SELECT {count_query} FROM {name}").fetchdf().iloc[0].to_dict()
    except Exception:
        unique_counts = {}

    row_count = len(df)
    stats = []
    for col in df.columns:
        ser = df[col]
        null_count = int(ser.isna().sum())
        stats.append({
            "column_name": col,
            "data_type": duckdb_types.get(col, str(ser.dtype)),
            "null_count": null_count,
            "null_pct": round(null_count / row_count, 3) if row_count else 0,
            "unique_count": int(unique_counts.get(col, ser.nunique(dropna=True))),
            "sample_values": ser.dropna().astype(str).head(5).tolist(),
        })
    return stats


def _get_sample_rows(con, name: str, n: int = 5) -> list[dict]:
    try:
        df = con.execute(f"SELECT * FROM {name} LIMIT {n}").fetchdf()
        return df.to_dict(orient="records")
    except Exception:
        logger.warning(f"Could not fetch sample rows for {name}")
        return []


def _llm_describe_table(llm, table_name: str, col_stats: list[dict], sample_rows: list[dict]) -> str:
    cols_str = "\n".join(
        f"  - {c['column_name']} ({c['data_type']}): {c['unique_count']} unique values, "
        f"{c['null_pct']*100:.1f}% null, sample: {c['sample_values']}"
        for c in col_stats
    )
    sample_str = json.dumps(sample_rows, default=str, indent=2)
    prompt = f"""You are a data analyst. Given the table name, its columns, and sample data,
write a single concise sentence describing what this table contains and its purpose.
Do not mention SQL, data types, or technical details. Be functional and clear.

Table: {table_name}
Columns:
{cols_str}

Sample rows:
{sample_str}

Return only the description sentence, nothing else."""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content.strip()
    except Exception as e:
        logger.warning(f"LLM table description failed for {table_name}: {e}")
        return ""


def _llm_describe_columns(llm, table_name: str, table_description: str,
                           col_stats: list[dict], sample_rows: list[dict]) -> dict[str, str]:
    cols_str = "\n".join(
        f"  - {c['column_name']} ({c['data_type']}): sample values: {c['sample_values']}"
        for c in col_stats
    )
    sample_str = json.dumps(sample_rows, default=str, indent=2)
    prompt = f"""You are a data analyst. Given a table and its columns, write a short functional description
for each column. Descriptions should help someone write SQL queries against this table.
Do not mention data types. Be concise — one sentence per column.

Table: {table_name}
Table description: {table_description}

Columns:
{cols_str}

Sample rows:
{sample_str}

Return a JSON object mapping column_name to its description. Example:
{{
  "order_id": "Unique identifier for each order.",
  "user_id": "Identifier of the user who placed the order."
}}
Return only the JSON object, nothing else."""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip().strip("```json").strip("```").strip()
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"LLM column description failed for {table_name}: {e}")
        return {}


def _build_table_schema_string(name: str, kind: str, row_count: int, col_stats: list[dict],
                                table_desc: str, col_descs: dict[str, str]) -> str:
    lines = [f"{'TABLE' if kind == 'table' else 'VIEW'}: {name} ({row_count:,} rows)"]
    if table_desc:
        lines.append(f"  Description: {table_desc}")
    lines.append("  Columns:")
    for c in col_stats:
        col_desc = col_descs.get(c["column_name"], "")
        desc_suffix = f" — {col_desc}" if col_desc else ""
        lines.append(
            f"    {c['column_name']} ({c['data_type']})"
            f"  [unique: {c['unique_count']}, null%: {c['null_pct']*100:.1f}]"
            f"  sample: {c['sample_values']}"
            f"{desc_suffix}"
        )
    return "\n".join(lines)


def build_schema_cache(force: bool = False) -> str:
    if SCHEMA_CACHE_PATH.exists() and not force:
        logger.info("Schema cache found, loading from disk.")
        return json.loads(SCHEMA_CACHE_PATH.read_text())["schema_string"]

    logger.info("Building schema cache from DuckDB + LLM ...")
    llm = get_llm()
    con = _get_connection()
    all_blocks = []
    cache_data = {"tables": {}}

    all_names = (
        [(n, "table") for n in TABLES_AND_VIEWS["tables"]] +
        [(n, "view") for n in TABLES_AND_VIEWS["views"]]
    )

    for name, kind in all_names:
        logger.info(f"  Processing {kind}: {name}")
        row_count = _get_row_count(con, name)
        col_stats = _get_column_stats(con, name)
        sample_rows = _get_sample_rows(con, name)
        table_desc = _llm_describe_table(llm, name, col_stats, sample_rows)
        col_descs = _llm_describe_columns(llm, name, table_desc, col_stats, sample_rows)
        schema_block = _build_table_schema_string(name, kind, row_count, col_stats, table_desc, col_descs)
        all_blocks.append(schema_block)

        cache_data["tables"][name] = {
            "kind": kind,
            "row_count": row_count,
            "col_stats": col_stats,
            "sample_rows": sample_rows,
            "table_description": table_desc,
            "column_descriptions": col_descs,
            "schema_block": schema_block,
        }

    con.close()

    schema_string = "=== INSTACART DATABASE SCHEMA ===\n\n"
    schema_string += "\n\n".join(all_blocks)
    schema_string += "\n\n" + USAGE_NOTES

    cache_data["schema_string"] = schema_string
    SCHEMA_CACHE_PATH.write_text(json.dumps(cache_data, indent=2, default=str))
    logger.info(f"Schema cache saved to {SCHEMA_CACHE_PATH}")

    return schema_string


def get_schema_string() -> str:
    return build_schema_cache(force=False)