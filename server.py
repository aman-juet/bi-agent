import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
import config
from agent.graph import app_graph
from agent.state import AgentState

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="BI Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="frontend"), name="static")


class ChatRequest(BaseModel):
    query: str
    thread_id: str

    @field_validator("query")
    @classmethod
    def query_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query must not be empty")
        return v.strip()


class ChatResponse(BaseModel):
    thread_id: str
    intent: str
    response: str
    sql: str
    result_data: list[dict]
    result_columns: list[str]
    plot_config: dict
    retry_count: int
    error: str


@app.get("/")
def serve_frontend():
    return FileResponse("frontend/index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    logger.info(f"thread={request.thread_id[:8]} | query='{request.query}'")
    try:
        config_obj = {"configurable": {"thread_id": request.thread_id}}
        initial_state: AgentState = {
            "user_query": request.query,
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

        result = app_graph.invoke(initial_state, config=config_obj)
        intent = result.get("intent", "")
        retry_count = result.get("retry_count", 0)

        logger.info(f"thread={request.thread_id[:8]} | intent={intent} | retries={retry_count}")
        if result.get("error"):
            logger.warning(f"thread={request.thread_id[:8]} | error={result['error']}")

        return ChatResponse(
            thread_id=request.thread_id,
            intent=intent,
            response=result.get("response_text", ""),
            sql=result.get("sql", ""),
            result_data=result.get("result_data", []),
            result_columns=result.get("result_columns", []),
            plot_config=result.get("plot_config", {}),
            retry_count=retry_count,
            error=result.get("error", ""),
        )

    except Exception as e:
        logger.exception(f"thread={request.thread_id[:8]} | unhandled error: {e}")
        raise HTTPException(status_code=500, detail=str(e))