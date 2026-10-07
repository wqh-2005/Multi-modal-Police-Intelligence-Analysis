from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .config import LLMSettings


class EntityDraft(BaseModel):
    name: str = Field(description="原文中的实体名称或规范称呼")
    type: str = Field(description="实体类型标准ID或对应中文名称")
    role: Optional[str] = Field(
        default=None, description="人物角色标准ID或对应中文名称；非人员实体必须为null"
    )


class TripleDraft(BaseModel):
    head: EntityDraft
    relation: str = Field(description="关系标准ID或对应中文名称")
    tail: EntityDraft
    factuality: str = Field(description="事实状态标准ID或对应中文名称")
    evidence_unit_ids: list[str] = Field(description="支持关系的来源单元ID")
    confidence: float = Field(ge=0, le=1)


class ExtractionResult(BaseModel):
    source_id: str
    triples: list[TripleDraft]


@dataclass(slots=True)
class ExtractionCall:
    result: ExtractionResult
    usage: dict[str, int]


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(getattr(block, "text", None), str):
                parts.append(block.text)
        return "".join(parts)
    return str(content)


def _parse_json(content: Any) -> dict[str, Any]:
    text = _content_text(content).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S | re.I)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("模型响应中没有JSON对象")
        text = text[start : end + 1]
    return json.loads(text)


def _token_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None) or {}
    if not usage:
        usage = getattr(response, "response_metadata", {}).get("token_usage", {})
    input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
    output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    total_tokens = int(usage.get("total_tokens", 0) or 0) or input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


class LLMClient:
    def __init__(self, system_prompt: str, settings: LLMSettings):
        self.model_name = settings.EXTRACTION_MODEL
        model_kwargs: dict[str, Any] = {}
        extra_body: dict[str, Any] = {}
        if settings.EXTRACTION_MODEL.startswith("Qwen/Qwen3"):
            # 关系抽取只需要确定性的结构化结果，关闭思考可避免长时间推理和额外Token。
            extra_body["enable_thinking"] = False
            model_kwargs["response_format"] = {"type": "json_object"}
        self.llm = ChatOpenAI(
            api_key=settings.EXTRACTION_API_KEY,
            base_url=settings.EXTRACTION_BASE_URL,
            model=settings.EXTRACTION_MODEL,
            temperature=0,
            max_tokens=settings.LLM_MAX_TOKENS,
            timeout=settings.LLM_TIMEOUT,
            max_retries=0,
            default_headers={"Accept-Encoding": "gzip, deflate"},
            model_kwargs=model_kwargs,
            extra_body=extra_body,
        )
        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                (
                    "human",
                    "实体类型与人物角色：\n{entity_schema}\n\n"
                    "关系类型与事实状态：\n{relation_schema}\n\n"
                    "待处理文档：\n{document}\n\n"
                    "只返回JSON对象，格式为："
                    '{{"source_id":"原文编号","triples":['
                    '{{"head":{{"name":"实体名","type":"类型ID","role":"角色ID或null"}},'
                    '"relation":"关系ID",'
                    '"tail":{{"name":"实体名","type":"类型ID","role":"角色ID或null"}},'
                    '"factuality":"事实状态ID",'
                    '"evidence_unit_ids":["来源单元ID"],"confidence":0.95}}]}}',
                ),
            ]
        )

    async def extract(
        self,
        document: str,
        entity_schema: str,
        relation_schema: str,
    ) -> ExtractionCall:
        response = await (self.prompt | self.llm).ainvoke(
            {
                "document": document,
                "entity_schema": entity_schema,
                "relation_schema": relation_schema,
            }
        )
        parsed = ExtractionResult.model_validate(_parse_json(response.content))
        return ExtractionCall(result=parsed, usage=_token_usage(response))
