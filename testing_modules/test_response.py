
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.state import AgentState
from agent.nodes import classifier_node, sql_generator_node, response_node


def run_test(query: str):
    state: AgentState = {
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
        "result_df": None,
        "plot_config": {},
        "response_text": "",
        "messages": [],
    }

    state = classifier_node(state)
    state = sql_generator_node(state)
    state = response_node(state)

    print(f"Query        : {query}")
    print(f"Plot needed  : {state['plot_needed']}")
    print(f"Response     : {state['response_text']}")
    print(f"Plot config  : {state['plot_config']}")
    print()


run_test("what are the top 10 most reordered products?")
run_test("which department has the highest reorder rate?")
run_test("how many orders were placed on each day of the week?")
run_test("what is the average basket size per order?")