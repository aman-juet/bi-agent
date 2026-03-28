from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uuid
from agent.graph import app_graph
from agent.state import AgentState


def run(query: str, thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    initial_state: AgentState = {
        "user_query": query,
        "intent": "",
        "is_followup": False,
        "plot_needed": False,
        "plot_type": "none",
        "table_names": [],
        "metadata_context": "",
        "sql": "",
        "retry_count": 0,
        "error": "",
        "result_data": [],
        "result_columns": [],
        "plot_config": {},
        "response_text": "",
        "messages": [],
    }
    result = app_graph.invoke(initial_state, config=config)
    print(f"Query    : {query}")
    print(f"Intent   : {result['intent']}")
    print(f"Response : {result['response_text']}")
    print(f"Plot     : {result['plot_config']}")
    print()
    return result


thread_id = str(uuid.uuid4())

print("=== chit chat ===")
run("hey there!", thread_id)

print("=== out of scope ===")
run("how do I make biryani?", thread_id)

print("=== data query ===")
run("what are the top 5 most reordered products?", thread_id)

print("=== followup ===")
run("now show me only the ones from the produce department", thread_id)