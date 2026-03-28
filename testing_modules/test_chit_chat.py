
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.state import AgentState
from agent.nodes import chit_chat_node

test_state: AgentState = {
    "user_query": "hey, how are you?",
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

result = chit_chat_node(test_state)
print("Response:", result["response_text"])
print("Messages:", result["messages"])