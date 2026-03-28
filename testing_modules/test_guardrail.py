
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.state import AgentState
from agent.nodes import guardrail_node

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
    result = guardrail_node(state)
    print(f"Query    : {query}")
    print(f"Blocked  : {result['intent'] == 'out_of_scope'}")
    print(f"Response : {result['response_text'] or '(not blocked, passing through)'}")
    print()

run_test("how do I make pasta carbonara?")
run_test("what are the top 10 most ordered products?")
run_test("who won the IPL last year?")
run_test("show me reorder rates by department")
run_test("what can you tell me about shopping patterns?")