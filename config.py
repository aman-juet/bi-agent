from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE_DIR = Path(__file__).parent
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
DB_PATH = BASE_DIR / "db" / "instacart.db"

CSV_FILES = {
    "orders": DATA_RAW_DIR / "orders.csv",
    "order_products_prior": DATA_RAW_DIR / "order_products__prior.csv",
    "order_products_train": DATA_RAW_DIR / "order_products__train.csv",
    "products": DATA_RAW_DIR / "products.csv",
    "aisles": DATA_RAW_DIR / "aisles.csv",
    "departments": DATA_RAW_DIR / "departments.csv",
}

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = "gpt-4.1"
OPENAI_MINI_MODEL = "gpt-4o-mini"
MAX_RESULT_ROWS = 1000
MAX_RETRIES = 2

if not OPENAI_API_KEY:
    raise EnvironmentError(
        "OPENAI_API_KEY not set. Add it to your .env file: OPENAI_API_KEY=sk-..."
    )

if os.environ.get("LANGCHAIN_API_KEY"):
    os.environ["LANGCHAIN_TRACING_V2"] = os.environ.get("LANGCHAIN_TRACING_V2", "true")
    os.environ["LANGCHAIN_PROJECT"] = os.environ.get("LANGCHAIN_PROJECT", "bi-agent")