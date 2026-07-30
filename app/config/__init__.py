# app/config/__init__.py
"""
配置模块
统一导出所有配置对象
"""

from app.config.config import (
    SYSTEM_PROMPT,
    com_cfg,
    llm_cfg,
    rag_raw,
    rag_cfg,
)

__all__ = [
    "SYSTEM_PROMPT",
    "com_cfg",
    "llm_cfg",
    "rag_raw",
    "rag_cfg",
]