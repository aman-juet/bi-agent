import logging
import pandas as pd
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from schemas.schemas import GuardrailOutput, ClassifierOutput, ResponseOutput
from agent.state import AgentState
from agent.tools import metadata_retriever, query_executor
from utils.llm_client import get_structured_llm, get_llm
from utils.prompt_loader import load_prompt
from utils.tracer import (
    trace_node_entry, trace_guardrail, trace_classifier,
    trace_metadata_retrieval, trace_sql_attempt,
    trace_sql_result, trace_response
)
from config import MAX_RETRIES

logger = logging.getLogger(__name__)


def _build_conversation_history(messages: list[BaseMessage], last_n: int = 6) -> str:
    if not messages:
        return "No prior conversation."
    lines = []
    for msg in messages[-last_n:]:
        role = "User" if isinstance(msg, HumanMessage) else "Assistant"
        lines.append(f"{role}: {msg.content}")
    return "\n".join(lines)


def guardrail_node(state: AgentState) -> AgentState:
    trace_node_entry("guardrail_node", state["user_query"])
    prompt = load_prompt("guardrail")
    llm = get_structured_llm(GuardrailOutput)

    system_content = prompt["system"].replace(
        "{conversation_history}", _build_conversation_history(state["messages"])
    )
    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=state["user_query"]),
    ]

    result: GuardrailOutput = llm.invoke(messages)
    trace_guardrail(result.intent, result.response_text)

    updated_messages = state["messages"]
    if result.intent in ("chit_chat", "out_of_scope"):
        updated_messages = state["messages"] + [
            HumanMessage(content=state["user_query"]),
            AIMessage(content=result.response_text),
        ]

    return {
        **state,
        "intent": result.intent,
        "response_text": result.response_text,
        "messages": updated_messages,
    }


def classifier_node(state: AgentState) -> AgentState:
    trace_node_entry("classifier_node", state["user_query"])
    prompt = load_prompt("classifier")
    llm = get_structured_llm(ClassifierOutput)

    system_content = prompt["system"].replace(
        "{conversation_history}", _build_conversation_history(state["messages"])
    )
    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=state["user_query"]),
    ]

    result: ClassifierOutput = llm.invoke(messages)
    trace_classifier(result.is_followup, result.plot_needed,
                     result.plot_type, result.table_names)

    metadata_context = ""
    if result.table_names:
        metadata_context = metadata_retriever.invoke({"table_names": result.table_names})
        trace_metadata_retrieval(result.table_names, metadata_context)

    return {
        **state,
        "is_followup": result.is_followup,
        "plot_needed": result.plot_needed,
        "plot_type": result.plot_type,
        "table_names": result.table_names,
        "metadata_context": metadata_context,
    }


def sql_generator_node(state: AgentState) -> AgentState:
    trace_node_entry("sql_generator_node", state["user_query"])
    prompt = load_prompt("sql_generator")
    llm = get_llm()

    conversation_history = _build_conversation_history(state["messages"])
    prior_sql = f"Previous SQL query:\n{state['sql']}" if state["is_followup"] and state["sql"] else ""
    error_feedback = ""
    current_sql = state.get("sql", "")

    for attempt in range(MAX_RETRIES + 1):
        error_feedback_str = (
            f"The previous query failed with this error:\n{error_feedback}\n"
            f"Previous SQL:\n{current_sql}\n"
            f"Please fix and return only the corrected SQL."
            if error_feedback else ""
        )

        system_content = (
            prompt["system"]
            .replace("{metadata_context}", state["metadata_context"])
            .replace("{conversation_history}", conversation_history)
            .replace("{prior_sql}", prior_sql or "None")
            .replace("{error_feedback}", error_feedback_str or "None")
        )

        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=state["user_query"]),
        ]

        response = llm.invoke(messages)
        raw_sql = response.content.strip()

        if raw_sql.startswith("```"):
            raw_sql = raw_sql.split("```")[1]
            if raw_sql.lower().startswith("sql"):
                raw_sql = raw_sql[3:]
        current_sql = raw_sql.strip()

        trace_sql_attempt(attempt, current_sql)
        exec_result = query_executor.invoke({"sql": current_sql})
        trace_sql_result(exec_result["success"], exec_result.get("row_count", 0), exec_result.get("error", ""))

        if exec_result["success"]:
            return {
                **state,
                "sql": current_sql,
                "result_data": exec_result["data"],
                "result_columns": exec_result["columns"],
                "error": "",
                "retry_count": attempt,
            }

        error_feedback = exec_result["error"]

    logger.error(f"sql_generator_node | all {MAX_RETRIES + 1} attempts failed")
    return {
        **state,
        "sql": current_sql,
        "result_data": [],
        "result_columns": [],
        "error": error_feedback,
        "retry_count": MAX_RETRIES,
    }


def response_node(state: AgentState) -> AgentState:
    trace_node_entry("response_node", state["user_query"])

    if state.get("error"):
        error_text = (
            f"I was unable to answer your question after {MAX_RETRIES} attempts. "
            f"Last error: {state['error']}"
        )
        logger.warning(f"response_node | returning error response")
        return {
            **state,
            "response_text": error_text,
            "plot_config": {},
            "messages": state["messages"] + [
                HumanMessage(content=state["user_query"]),
                AIMessage(content=error_text),
            ],
        }

    prompt = load_prompt("response")
    llm = get_structured_llm(ResponseOutput)

    df = pd.DataFrame(state["result_data"], columns=state["result_columns"]) if state["result_data"] else None
    data_sample = df.head(20).to_string(index=False) if df is not None else "No data."

    system_content = (
        prompt["system"]
        .replace("{user_query}", state["user_query"])
        .replace("{sql}", state["sql"])
        .replace("{plot_needed}", str(state["plot_needed"]))
        .replace("{plot_type}", state["plot_type"])
        .replace("{data_sample}", data_sample)
    )

    messages = [SystemMessage(content=system_content)]
    result: ResponseOutput = llm.invoke(messages)
    plot_config = result.plot_config.model_dump() if result.plot_config else {}

    trace_response(result.response_text, plot_config)

    return {
        **state,
        "response_text": result.response_text,
        "plot_config": plot_config,
        "messages": state["messages"] + [
            HumanMessage(content=state["user_query"]),
            AIMessage(content=result.response_text),
        ],
    }