"""主应用入口。

端到端流水线接口（唯一暴露）:
    POST /api/v1/pipeline — 多模态输入 → 知识抽取 → 知识存储 → 智能研判 → 预警输出
"""
import asyncio
import time
from logging import getLogger
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.models.multimodal_schema import BatchMultimodalRequest, BatchMultimodalResponse
from app.core.multimodal.service import process_batch_task
from app.core.knowledge.extraction_service import run_extraction
from app.core.knowledge.storage_service import run_storage
from app.core.alertoutput.alertoutput import AlertOutput
from app.core.judgment.judger import Judger
from fastapi.middleware.cors import CORSMiddleware

# # 原各模块路由（测试时可取消注释）
# from app.api.multimodal_api import router as multimodal_router
# from app.api.knowledge import router as knowledge_router
# from app.api.intelligentjudge import router as judge_router

# app = FastAPI()

# # # 注册多模态模块路由（第一模块）
# app.include_router(multimodal_router)

# # 注册知识抽取与知识图谱路由（模块二/三）
# app.include_router(knowledge_router)


# #注册智能研判和预警输出路由（模块四/五）
# app.include_router(judge_router)

logger = getLogger(__name__)

app = FastAPI(
    title="多模态警务智能分析系统",
    description="端到端流水线：多模态识别 → 知识抽取 → 知识图谱存储 → 智能研判 → 预警输出",
    version="1.0.0",
)


class PipelineResponse(BaseModel):
    """端到端流水线响应。"""
    case_id: str = Field(description="案件编号")
    judgment: dict = Field(description="研判结果")
    alerts: List[dict] = Field(description="预警列表")
    deepfake_detected: bool = Field(description="是否检测到 AI 换脸")
    elapsed_ms: float = Field(description="总耗时（毫秒）")
    stages: dict = Field(description="各阶段耗时明细")


_EMPTY_TEXT_PLACEHOLDER = "此文本为空"


def _merge_texts(multimodal_result: BatchMultimodalResponse) -> str:
    """从多模态输出中合并所有有效文本。"""
    parts = []
    for item in multimodal_result.outputs:
        if item.status in ("done", "success") and item.text and item.text != _EMPTY_TEXT_PLACEHOLDER:
            parts.append(item.text.strip())
    return "\n".join(parts)


def _check_deepfake(multimodal_result: BatchMultimodalResponse) -> bool:
    """检查是否检测到 AI 换脸。"""
    return any(item.deepfake_result is True for item in multimodal_result.outputs)


def _init_knowledge_base():
    """初始化 RAG 知识库（幂等）。"""
    try:
        Judger.init_knowledge_base()
    except Exception as e:
        logger.warning("知识库初始化失败（可能已初始化或文件缺失）: %s", str(e))

        

@app.post("/api/v1/pipeline", response_model=PipelineResponse, tags=["端到端流水线"])
async def pipeline(data: BatchMultimodalRequest):
    """端到端流水线：多模态输入 → 智能研判输出。

    一步完成四个模块的串联处理：
    1. 多模态识别（OCR / 语音转写）
    2. 知识抽取（LLM 三元组抽取）
    3. 知识图谱存储（Neo4j 写入）
    4. 智能研判与预警输出

    Args:
        data: 用户提交的多模态输入（格式 1.1），包含 case_id 和 inputs 列表。

    Returns:
        PipelineResponse: 研判结果 + 预警列表 + 各阶段耗时。
    """
    t0 = time.time()
    stages = {}

    # ── 阶段 1：多模态识别 ──
    t1 = time.time()
    try:
        multimodal_result = await process_batch_task(data)
    except Exception as e:
        # logger.exception("多模态识别失败: case_id=%s", data.case_id)
        raise HTTPException(status_code=500, detail=f"多模态识别失败: {str(e)}")
    stages["multimodal_ms"] = round((time.time() - t1) * 1000)

    merged_text = _merge_texts(multimodal_result)
    deepfake_alert = _check_deepfake(multimodal_result)

    if not merged_text:
        raise HTTPException(status_code=400, detail="所有模态输出文本均为空，无法继续")

    # ── 阶段 2：知识抽取 ──
    t2 = time.time()
    try:
        extraction = await run_extraction(
            merged_text,
            case_id=data.case_id,
            deepfake_alert=deepfake_alert,
        )
    except TimeoutError:
        raise HTTPException(status_code=502, detail="LLM 调用超时")
    except Exception as e:
        # logger.exception("知识抽取失败: case_id=%s", data.case_id)
        raise HTTPException(status_code=500, detail=f"知识抽取失败: {str(e)}")
    stages["extraction_ms"] = round((time.time() - t2) * 1000)

    # ── 阶段 3：知识图谱存储 ──
    t3 = time.time()
    try:
        storage = await run_storage(extraction)
    except Exception as e:
        err_msg = str(e).lower()
        if "couldn't connect" in err_msg or "service unavailable" in err_msg:
            raise HTTPException(status_code=502, detail="图数据库 Neo4j 不可达")
        # logger.exception("知识存储失败: case_id=%s", data.case_id)
        raise HTTPException(status_code=500, detail=f"知识存储失败: {str(e)}")
    stages["storage_ms"] = round((time.time() - t3) * 1000)

    # ── 阶段 4：智能研判 ──
    t4 = time.time()
    _init_knowledge_base()
    neo4j_dict = storage.model_dump(by_alias=True)
    try:
        result = await asyncio.to_thread(AlertOutput().generate, neo4j_dict)
    except Exception as e:
        # logger.exception("研判失败: case_id=%s", data.case_id)
        raise HTTPException(status_code=500, detail=f"研判失败: {str(e)}")
    stages["judgment_ms"] = round((time.time() - t4) * 1000)

    total_ms = round((time.time() - t0) * 1000)
    stages["total_ms"] = total_ms

    logger.info(
        "流水线完成: case_id=%s, multimodal=%dms, extraction=%dms, storage=%dms, judgment=%dms, total=%dms",
        data.case_id,
        stages["multimodal_ms"],
        stages["extraction_ms"],
        stages["storage_ms"],
        stages["judgment_ms"],
        total_ms,
    )

    return PipelineResponse(
        case_id=result["case_id"],
        judgment=result["judgment"],
        alerts=result["alerts"],
        deepfake_detected=result["deepfake_detected"],
        elapsed_ms=total_ms,
        stages=stages,
    )
