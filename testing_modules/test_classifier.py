
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.state import AgentState
from agent.nodes import classifier_node
from langchain_core.messages import HumanMessage, AIMessage


def run_test(query: str, messages: list = []):
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
        "messages": messages,
    }
    result = classifier_node(state)
    print(f"Query          : {query}")
    print(f"Intent         : {result['intent']}")
    print(f"Is followup    : {result['is_followup']}")
    print(f"Plot needed    : {result['plot_needed']}")
    print(f"Plot type      : {result['plot_type']}")
    print(f"Tables needed  : {result['table_names']}")
    print(f"Metadata fetched: {len(result['metadata_context'])} chars")
    print()


run_test("what are the top 10 most reordered products?")
run_test("show me order volume by day of week as a chart")
run_test("which department has the highest reorder rate?")
run_test("now filter that to only the produce department", messages=[
    HumanMessage(content="which department has the highest reorder rate?"),
    AIMessage(content="The produce department has the highest reorder rate at 0.67."),
])
run_test("how many unique users placed orders on saturday?")