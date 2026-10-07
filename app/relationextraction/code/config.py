from __future__ import annotations

import os
import warnings
from collections import Counter
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


CODE_DIR = Path(__file__).resolve().parent
MODULE_ROOT = CODE_DIR.parent
PROJECT_ROOT = MODULE_ROOT.parents[1]
ENV_FILE = PROJECT_ROOT / ".env"


def _parse_compatible_env() -> tuple[dict[str, str], list[str]]:
    """读取标准dotenv与PowerShell `$env:KEY=value`，同文件后值覆盖前值。"""
    values: dict[str, str] = {}
    keys: list[str] = []
    if not ENV_FILE.exists():
        return values, []
    for raw_line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("$env:"):
            line = line[5:]
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            keys.append(key)
            values[key] = value
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
    return values, duplicates


def load_compatible_env() -> list[str]:
    """加载配置但不覆盖进程已显式设置的环境变量。"""
    values, duplicates = _parse_compatible_env()
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return duplicates


class LLMSettings(BaseSettings):
    EXTRACTION_API_KEY: str
    EXTRACTION_BASE_URL: str
    EXTRACTION_MODEL: str
    LLM_MAX_TOKENS: int = 4096
    LLM_TIMEOUT: int = 240
    LLM_MAX_RETRIES: int = 3

    model_config = SettingsConfigDict(extra="ignore")


class PathSettings:
    DIALOGUE_FILE = PROJECT_ROOT / "data" / "raw" / "对话数据集.json"
    CASE_FILE = PROJECT_ROOT / "data" / "raw" / "诈骗案例数据集_带编号.json"
    ENTITY_SCHEMA = MODULE_ROOT / "schemas" / "实体类型体系_v0.1_中文可读版.json"
    RELATION_SCHEMA = MODULE_ROOT / "schemas" / "关系类型体系_v0.1_中文可读版.json"
    PROMPT_FILE = MODULE_ROOT / "prompts" / "一体化三元组抽取_v1.0.txt"
    OUTPUT_DIR = MODULE_ROOT / "output"


path_cfg = PathSettings()


@lru_cache(maxsize=1)
def get_llm_settings() -> LLMSettings:
    duplicates = load_compatible_env()
    if duplicates:
        warnings.warn(
            f".env存在重复配置（按最后一项生效）：{', '.join(duplicates)}",
            RuntimeWarning,
            stacklevel=2,
        )
    settings = LLMSettings()
    if settings.LLM_MAX_TOKENS < 4096:
        warnings.warn(
            f"LLM_MAX_TOKENS={settings.LLM_MAX_TOKENS}，长文档输出可能被截断，建议至少4096",
            RuntimeWarning,
            stacklevel=2,
        )
    return settings


def configuration_audit() -> dict[str, object]:
    """返回不包含密钥值的配置检查信息。"""
    values, duplicates = _parse_compatible_env()
    required = ("EXTRACTION_API_KEY", "EXTRACTION_BASE_URL", "EXTRACTION_MODEL")
    optional = ("LLM_MAX_TOKENS", "LLM_TIMEOUT", "LLM_MAX_RETRIES")
    return {
        "env_file": str(ENV_FILE),
        "env_file_exists": ENV_FILE.exists(),
        "required_keys": {key: bool(os.getenv(key) or values.get(key)) for key in required},
        "optional_keys": {key: bool(os.getenv(key) or values.get(key)) for key in optional},
        "duplicate_keys": duplicates,
    }
