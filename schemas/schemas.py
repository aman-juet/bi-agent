from pydantic import BaseModel, Field
from typing import Literal, Optional


class GuardrailOutput(BaseModel):
    intent: Literal["chit_chat", "out_of_scope", "data_query"] = Field(
        description="The intent of the user query."
    )
    response_text: str = Field(
        description="Response for chit_chat or out_of_scope intents. Empty string for data_query."
    )


class ClassifierOutput(BaseModel):
    is_followup: bool = Field(
        description="True if the query references a previous result or continues a prior question."
    )
    plot_needed: bool = Field(
        description="True if the answer would benefit from a chart or visualization."
    )
    plot_type: Literal["bar", "line", "pie", "scatter", "none"] = Field(
        description="The most appropriate chart type for the query. Use none if plot_needed is False."
    )
    table_names: list[str] = Field(
        description="List of table or view names needed to answer the query. Choose only from the allowed list."
    )


class PlotConfig(BaseModel):
    chart_type: Literal["bar", "line", "pie", "scatter", "none"] = Field(
        description="Type of chart to render."
    )
    x_column: Optional[str] = Field(
        description="Column name to use for the x-axis or labels."
    )
    y_column: Optional[str] = Field(
        description="Column name to use for the y-axis or values."
    )
    title: str = Field(
        description="A short descriptive title for the chart."
    )


class ResponseOutput(BaseModel):
    response_text: str = Field(
        description="A concise natural language summary of the query results for the user."
    )
    plot_config: Optional[PlotConfig] = Field(
        default=None,
        description="Plot configuration if a chart is needed. None if no chart required."
    )