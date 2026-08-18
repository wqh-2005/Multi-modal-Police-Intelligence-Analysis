import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent.parent

#读取环境
# APP_ENV = os.getenv("APP_ENV","dev")
ENV_FILE = PROJECT_ROOT / ".env"

PROMPT_FILE_PATH = PROJECT_ROOT / "system_prompt.txt"

# 读取提示词（utf8，捕获异常方便调试）
try:
    with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
except FileNotFoundError:
    raise Exception(f"提示词文件不存在！路径：{PROMPT_FILE_PATH.resolve()}")

class LLMSettings(BaseSettings):
    LLMMODEL: str

    model_config = SettingsConfigDict(
        env_file = ENV_FILE,
        env_file_encoding = "utf-8",
        extra="ignore",
    )

class RagEngineRaw(BaseSettings):
    RAGENGING_MODEL: str
    EXAMPLE_JSON_PATH: str
    JSON_PROCESSED: str
    REAL_JSON_PATH_1: str
    REAL_JSON_PATH_2: str
    REAL_JSON_PROCESSED: str
    RAG_COLLECTION: str

    model_config = SettingsConfigDict(
        env_file = ENV_FILE,
        env_file_encoding = "utf-8",
        extra="ignore",
    )

class ComSetting(BaseSettings):
    JUDGMENT_API_KEY: str
    JUDGMENT_BASE_URL: str
    RAG_TOP_K: int
    LLM_MAX_TOKENS: int 

    model_config = SettingsConfigDict(
        env_file = ENV_FILE,
        env_file_encoding = "utf-8",
        extra="ignore"
    )

llm_cfg = LLMSettings()
rag_raw = RagEngineRaw()
com_cfg = ComSetting()


EXAMPLE_JSON_Dir_1 = PROJECT_ROOT / rag_raw.REAL_JSON_PATH_1
EXAMPLE_JSON_Dir_2 = PROJECT_ROOT / rag_raw.REAL_JSON_PATH_2
JSON_PROCESSED_DIR = PROJECT_ROOT / rag_raw.REAL_JSON_PROCESSED

class RagSettings:
    RAGENGING_MODEL: str = rag_raw.RAGENGING_MODEL
    EXAMPLE_JSON_DIRS: list = [EXAMPLE_JSON_Dir_1, EXAMPLE_JSON_Dir_2]
    JSON_PROCESSED_DIR: Path = JSON_PROCESSED_DIR
    RAG_COLLECTION: str = rag_raw.RAG_COLLECTION

rag_cfg = RagSettings()