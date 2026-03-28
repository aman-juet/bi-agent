from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel
from typing import Type
from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_MINI_MODEL


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=OPENAI_MODEL,
        temperature=0.0,
        api_key=OPENAI_API_KEY,
    )


def get_mini_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=OPENAI_MINI_MODEL,
        temperature=0.7,
        api_key=OPENAI_API_KEY,
    )


def get_structured_llm(schema: Type[BaseModel]) -> BaseChatModel:
    return get_llm().with_structured_output(schema)