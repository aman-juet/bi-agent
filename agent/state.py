from typing import Any
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    user_query: str
    intent: str
    is_followup: bool
    plot_needed: bool
    plot_type: str
    table_names: list[str]
    metadata_context: str
    sql: str
    retry_count: int
    error: str
    result_data: list[dict]      # was result_df — now plain list of dicts
    result_columns: list[str]    # column names preserved separately
    plot_config: dict
    response_text: str
    messages: list[BaseMessage]