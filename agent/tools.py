import json
import duckdb
import pandas as pd
from langchain_core.tools import tool
from config import DB_PATH
from utils.schema_builder import SCHEMA_CACHE_PATH

ALLOWED_TABLES = [
    "orders",
    "order_products",
    "product_full",
    "products",
    "aisles",
    "departments",
    "order_products_prior",
    "order_products_train",
]


@tool
def metadata_retriever(table_names: list[str]) -> str:
    """
    Retrieves schema metadata for the specified tables or views.
    Use this to get column names, types, descriptions, and sample values
    before generating a SQL query.
    Args:
        table_names: list of table or view names to retrieve metadata for.
    """
    if not SCHEMA_CACHE_PATH.exists():
        return "Schema cache not found. Please run schema_builder first."

    cache = json.loads(SCHEMA_CACHE_PATH.read_text())
    tables = cache.get("tables", {})

    invalid = [t for t in table_names if t not in ALLOWED_TABLES]
    if invalid:
        return f"Invalid table names requested: {invalid}. Allowed: {ALLOWED_TABLES}"

    blocks = []
    for name in table_names:
        if name in tables:
            blocks.append(tables[name].get("schema_block", f"No schema found for {name}"))
        else:
            blocks.append(f"No metadata found for table: {name}")

    return "\n\n".join(blocks)


@tool
def query_executor(sql: str) -> dict:
    """
    Executes a DuckDB SQL query and returns the result as a dictionary.
    Args:
        sql: a valid DuckDB SQL query string.
    Returns:
        dict with keys:
            - success: bool
            - data: list of row dicts (empty on failure)
            - columns: list of column names
            - row_count: int
            - error: error message string (empty on success)
    """
    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
        df = con.execute(sql).fetchdf()
        con.close()
        return {
            "success": True,
            "data": df.to_dict(orient="records"),
            "columns": list(df.columns),
            "row_count": len(df),
            "error": "",
        }
    except Exception as e:
        return {
            "success": False,
            "data": [],
            "columns": [],
            "row_count": 0,
            "error": str(e),
        }