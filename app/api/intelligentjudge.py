"""智能研判与预警输出 API 路由。

提供模块四的 REST 接口：接收上游知识图谱数据（格式 1.4），
执行研判并生成预警。

接口列表:
    POST   /api/v1/judge                — 单案例研判
    POST   /api/v1/judge/batch          — 批量研判
    GET    /api/v1/judge/health         — 健康检查
"""
import asyncio
import time
from logging import getLogger
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.knowledge_schema import GraphStorageOutput
from app.core.judgment.judger import Judger
from app.core.alertoutput.alertoutput import AlertOutput

logger = getLogger(__name__)

router = APIRouter(prefix="/api/v1/judge", tags=["智能研判与预警"])


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------

class TimedResponse(BaseModel):
    elapsed_ms: float = Field(description="接口处理耗时（毫秒）")


class JudgeResponse(TimedResponse):
    case_id: str = Field(description="案例唯一标识")
    judgment: dict = Field(description="研判结果")
    alerts: List[dict] = Field(description="预警列表")
    deepfake_detected: bool = Field(description="是否检测到 AI 换脸")


class BatchJudgeResponse(TimedResponse):
    total: int = Field(description="成功处理数")
    skipped: int = Field(description="跳过数（case_id 缺失）")
    results: List[dict] = Field(description="各案例研判与预警结果")


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "intelligent-judgment"


# ---------------------------------------------------------------------------
#  辅助
# ---------------------------------------------------------------------------

def _init_knowledge_base():
    try:
        Judger.init_knowledge_base()
    except Exception as e:
        logger.warning("知识库初始化失败（可能已初始化或文件缺失）: %s", str(e))


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse()


@router.post("", response_model=JudgeResponse)
async def judge(data: GraphStorageOutput):
    """单案例研判接口。

    接收上游知识图谱存储输出（格式 1.4），执行 RAG 检索 + LLM 研判，
    返回研判结果和预警列表。

    Args:
        data: GraphStorageOutput，来自 /api/v1/knowledge/pipeline 或 /store 的输出。

    Returns:
        JudgeResponse: 包含研判结果和预警列表。
    """
    t0 = time.time()

    _init_knowledge_base()

    neo4j_dict = data.model_dump(by_alias=True)

    try:
        result = await AlertOutput().generate(neo4j_dict)
    except Exception as e:
        logger.exception("研判失败: case_id=%s", data.case_id)
        raise HTTPException(status_code=500, detail=f"研判失败: {str(e)}")

    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info("研判完成: case_id=%s, is_fraud=%s, %dms",
                data.case_id, result["judgment"].get("is_fraud"), elapsed_ms)

    return JudgeResponse(
        case_id=result["case_id"],
        judgment=result["judgment"],
        alerts=result["alerts"],
        deepfake_detected=result["deepfake_detected"],
        elapsed_ms=elapsed_ms,
    )


@router.post("/batch", response_model=BatchJudgeResponse)
async def judge_batch(data: List[GraphStorageOutput]):
    """批量研判接口。

    接收多个案例，逐个执行研判，跳过 case_id 缺失的案例。

    Args:
        data: 多个 GraphStorageOutput 对象列表。

    Returns:
        BatchJudgeResponse: 包含各案例的研判结果。
    """
    t0 = time.time()

    _init_knowledge_base()

    alert_output = AlertOutput()

    neo4j_list = []
    for item in data:
        neo4j_list.append(item.model_dump(by_alias=True))

    result = await asyncio.to_thread(alert_output.generator_batch, neo4j_list)

    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info("批量研判完成: total=%d, skipped=%d, %dms",
                result["total"], result["skipped"], elapsed_ms)

    return BatchJudgeResponse(
        total=result["total"],
        skipped=result["skipped"],
        results=result["results"],
        elapsed_ms=elapsed_ms,
    )