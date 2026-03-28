import logging
import json
from typing import Any

logger = logging.getLogger("bi_agent.trace")


def _divider(char="─", width=60):
    return char * width


def trace_node_entry(node_name: str, query: str):
    logger.info(f"\n{_divider('═')}")
    logger.info(f"  NODE: {node_name.upper()}")
    logger.info(f"  QUERY: {query}")
    logger.info(_divider('═'))


def trace_guardrail(intent: str, response_text: str):
    logger.info(f"  INTENT DETECTED   : {intent.upper()}")
    if response_text:
        logger.info(f"  RESPONSE          : {response_text[:120]}...")
    logger.info(_divider())


def trace_classifier(is_followup: bool, plot_needed: bool,
                     plot_type: str, table_names: list[str]):
    logger.info(f"  IS FOLLOWUP       : {is_followup}")
    logger.info(f"  PLOT NEEDED       : {plot_needed}")
    logger.info(f"  PLOT TYPE         : {plot_type}")
    logger.info(f"  TABLES SELECTED   : {table_names}")
    logger.info(_divider())


def trace_metadata_retrieval(table_names: list[str], metadata_context: str):
    logger.info(f"  METADATA RETRIEVER CALLED")
    logger.info(f"  TABLES REQUESTED  : {table_names}")
    logger.info(f"  SCHEMA CHARS      : {len(metadata_context)}")
    logger.info(f"  TABLES IN CONTEXT :")
    for name in table_names:
        logger.info(f"    → {name}")
    logger.info(_divider())


def trace_sql_attempt(attempt: int, sql: str):
    logger.info(f"  SQL ATTEMPT       : {attempt + 1}")
    logger.info(f"  GENERATED SQL     :")
    for line in sql.strip().splitlines():
        logger.info(f"    {line}")
    logger.info(_divider())


def trace_sql_result(success: bool, row_count: int, error: str = ""):
    if success:
        logger.info(f"  SQL RESULT        : SUCCESS — {row_count} rows returned")
    else:
        logger.info(f"  SQL RESULT        : FAILED")
        logger.info(f"  ERROR             : {error}")
    logger.info(_divider())


def trace_response(response_text: str, plot_config: dict):
    logger.info(f"  RESPONSE SUMMARY  : {response_text[:150]}...")
    if plot_config and plot_config.get("chart_type") != "none":
        logger.info(f"  PLOT CONFIG       :")
        logger.info(f"    chart_type      : {plot_config.get('chart_type')}")
        logger.info(f"    x_column        : {plot_config.get('x_column')}")
        logger.info(f"    y_column        : {plot_config.get('y_column')}")
        logger.info(f"    title           : {plot_config.get('title')}")
    else:
        logger.info(f"  PLOT CONFIG       : none")
    logger.info(_divider('═'))