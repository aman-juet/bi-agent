import logging
import os
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from agent.state import AgentState
from agent.nodes import (
    guardrail_node,
    classifier_node,
    sql_generator_node,
    response_node,
)

logger = logging.getLogger(__name__)


def route_after_guardrail(state: AgentState) -> str:
    if state["intent"] in ("chit_chat", "out_of_scope"):
        logger.info(f"Graph exiting early — intent: {state['intent']}")
        return END
    return "classifier_node"


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("guardrail_node", guardrail_node)
    graph.add_node("classifier_node", classifier_node)
    graph.add_node("sql_generator_node", sql_generator_node)
    graph.add_node("response_node", response_node)

    graph.set_entry_point("guardrail_node")

    graph.add_conditional_edges(
        "guardrail_node",
        route_after_guardrail,
        {
            END: END,
            "classifier_node": "classifier_node",
        }
    )

    graph.add_edge("classifier_node", "sql_generator_node")
    graph.add_edge("sql_generator_node", "response_node")
    graph.add_edge("response_node", END)

    memory = MemorySaver()
    compiled = graph.compile(checkpointer=memory)
    logger.info("Graph compiled successfully.")
    return compiled


app_graph = build_graph()