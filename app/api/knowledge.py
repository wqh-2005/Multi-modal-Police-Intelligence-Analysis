"""知识抽取与知识图谱 API 路由。

提供模块二（知识抽取）和模块三（知识存储）的 REST 接口。

接口列表:
    POST   /api/v1/knowledge/extract    — 知识抽取（格式 1.2 → 1.3）
    POST   /api/v1/knowledge/store      — 知识存储（格式 1.3 → 1.4，写入 Neo4j）
    POST   /api/v1/knowledge/pipeline   — 端到端流水线（格式 1.2 → 1.4，抽取+存储合并）
    GET    /api/v1/knowledge/health     — 健康检查
"""
import time
from logging import getLogger

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.models.knowledge_schema import (
    ExtractionOutput,
    GraphStorageOutput,
    BatchMultimodalResponse,
)
from app.core.knowledge.extraction_service import run_extraction
from app.core.knowledge.storage_service import run_storage

logger = getLogger(__name__)

router = APIRouter(prefix="/api/v1/knowledge", tags=["知识抽取与图谱"])

_EMPTY_TEXT_PLACEHOLDER = "此文本为空"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _merge_texts(data: BatchMultimodalResponse) -> str:
    """从多模态输出中合并所有有效文本。

    Args:
        data: 模块一的多模态识别结果。

    Returns:
        合并后的文本，不同模态输出之间用换行分隔。
    """
    parts = []
    for item in data.outputs:
        # 兼容两种状态约定：
        #   "done"    — 格式 1.2 规范（第一模块 v2.0 文档，模块一实际输出）
        #   "success" — 早期约定（本地测试数据集 sample/test.json 等仍使用）
        if item.status in ("done", "success") and item.text and item.text != _EMPTY_TEXT_PLACEHOLDER:
            parts.append(item.text.strip())
    return "\n".join(parts)


def _check_deepfake(data: BatchMultimodalResponse) -> bool:
    """检查多模态输出中是否检测到换脸/伪造。

    Args:
        data: 模块一的多模态识别结果。

    Returns:
        是否任一输出检测到 deepfake。
    """
    return any(item.deepfake_result is True for item in data.outputs)


def _input_stats(data: BatchMultimodalResponse) -> dict:
    """统计多模态输入信息。

    Args:
        data: 模块一的多模态识别结果。

    Returns:
        统计摘要字典。
    """
    total = len(data.outputs)
    success = sum(1 for o in data.outputs if o.status == "success")
    errors = sum(1 for o in data.outputs if o.status == "error")
    types = [o.type for o in data.outputs]
    return {
        "total_outputs": total,
        "success_count": success,
        "error_count": errors,
        "modality_types": list(set(types)),
    }


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------

class TimedResponse(BaseModel):
    """带耗时信息的通用响应包装。"""
    elapsed_ms: float = Field(description="接口处理耗时（毫秒）")


class ExtractResponse(ExtractionOutput, TimedResponse):
    """知识抽取响应（格式 1.3 + 耗时）。"""
    pass


class StoreResponse(GraphStorageOutput, TimedResponse):
    """知识存储响应（格式 1.4 + 耗时）。"""
    pass


class PipelineResponse(GraphStorageOutput, TimedResponse):
    """端到端流水线响应（格式 1.4 + 耗时）。"""
    pass


class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str = "ok"
    service: str = "knowledge-extraction"
    neo4j_connected: bool = False


class ErrorDetail(BaseModel):
    """结构化错误信息。"""
    error_type: str
    message: str
    detail: str = ""


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查接口。

    检查服务存活状态及 Neo4j 连接。

    Returns:
        HealthResponse: 健康状态。
    """
    neo4j_ok = False
    try:
        from app.core.knowledge.storage_service import _get_driver
        driver = _get_driver()
        await driver.verify_connectivity()
        neo4j_ok = True
    except Exception:
        pass

    return HealthResponse(neo4j_connected=neo4j_ok)


@router.post("/extract", response_model=ExtractResponse)
async def extract_knowledge(data: BatchMultimodalResponse, request: Request = None):
    """知识抽取接口（格式 1.2 → 1.3）。

    接收模块一的多模态输出，合并所有成功文本后调用 LLM 抽取三元组。

    Args:
        data: 包含 case_id 和多模态识别结果列表。
        request: FastAPI Request 对象，用于日志。

    Returns:
        ExtractResponse: 格式 1.3 + 处理耗时。

    Raises:
        HTTPException 400: 所有有效文本均为空。
        HTTPException 502: LLM 调用超时或上游服务不可用。
        HTTPException 500: 其他内部错误。
    """
    t0 = time.time()
    merged_text = _merge_texts(data)

    if not merged_text:
        logger.warning("抽取请求文本为空, case_id=%s", data.case_id)
        raise HTTPException(status_code=400, detail="所有模态输出文本均为空，无法抽取")

    logger.info("抽取请求: case_id=%s, text_len=%d, stats=%s",
                data.case_id, len(merged_text), _input_stats(data))

    try:
        result = await run_extraction(
            merged_text,
            case_id=data.case_id,
            deepfake_alert=_check_deepfake(data),
        )
    except TimeoutError:
        logger.error("LLM 调用超时, case_id=%s", data.case_id)
        raise HTTPException(
            status_code=502,
            detail=ErrorDetail(
                error_type="LLM_TIMEOUT",
                message="大模型调用超时，请稍后重试",
                detail=f"case_id={data.case_id}",
            ).model_dump(),
        )
    except Exception as e:
        logger.exception("知识抽取异常, case_id=%s", data.case_id)
        raise HTTPException(
            status_code=500,
            detail=ErrorDetail(
                error_type="EXTRACTION_ERROR",
                message="知识抽取失败",
                detail=str(e),
            ).model_dump(),
        )

    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info("抽取完成: case_id=%s, triplets=%d, conf=%.2f, %dms",
                data.case_id, len(result.triplets), result.extraction_confidence, elapsed_ms)

    return ExtractResponse(
        triplets=result.triplets,
        raw_text=result.raw_text,
        extraction_confidence=result.extraction_confidence,
        case_id=result.case_id,
        deepfake_alert=result.deepfake_alert,
        elapsed_ms=elapsed_ms,
    )


@router.post("/store", response_model=StoreResponse, response_model_by_alias=True)
async def store_knowledge(data: ExtractionOutput):
    """知识存储接口（格式 1.3 → 1.4）。

    将抽取结果（三元组）写入 Neo4j 图数据库。

    Args:
        data: ExtractionOutput，来自 /extract 的输出。

    Returns:
        StoreResponse: 格式 1.4 + 处理耗时。

    Raises:
        HTTPException 502: Neo4j 连接不可用。
        HTTPException 500: 写入异常。
    """
    t0 = time.time()
    logger.info("存储请求: case_id=%s, triplets=%d", data.case_id, len(data.triplets))

    try:
        result = await run_storage(data)
    except Exception as e:
        err_msg = str(e).lower()
        if "couldn't connect" in err_msg or "service unavailable" in err_msg:
            logger.error("Neo4j 不可达, case_id=%s", data.case_id)
            raise HTTPException(
                status_code=502,
                detail=ErrorDetail(
                    error_type="NEO4J_UNAVAILABLE",
                    message="图数据库 Neo4j 不可达，请检查服务状态",
                    detail=str(e),
                ).model_dump(),
            )
        logger.exception("知识存储异常, case_id=%s", data.case_id)
        raise HTTPException(
            status_code=500,
            detail=ErrorDetail(
                error_type="STORAGE_ERROR",
                message="知识存储失败",
                detail=str(e),
            ).model_dump(),
        )

    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info("存储完成: case_id=%s, nodes=%d, rels=%d, %dms",
                data.case_id, len(result.relations), len(result.relations), elapsed_ms)

    return StoreResponse(
        victim=result.victim,
        suspect=result.suspect,
        relations=result.relations,
        transactions=result.transactions,
        chat_history=result.chat_history,
        deepfake_alert=result.deepfake_alert,
        case_id=result.case_id,
        elapsed_ms=elapsed_ms,
    )


@router.post("/pipeline", response_model=PipelineResponse, response_model_by_alias=True)
async def pipeline(data: BatchMultimodalResponse):
    """端到端流水线（格式 1.2 → 1.4）。

    一步完成抽取 + 存储，减少一次网络往返。

    Args:
        data: 包含 case_id 和多模态识别结果列表。

    Returns:
        PipelineResponse: 格式 1.4 + 处理耗时。

    Raises:
        HTTPException 400/502/500: 参照 /extract 和 /store。
    """
    t0 = time.time()
    merged_text = _merge_texts(data)

    if not merged_text:
        raise HTTPException(status_code=400, detail="所有模态输出文本均为空")

    try:
        extraction = await run_extraction(
            merged_text,
            case_id=data.case_id,
            deepfake_alert=_check_deepfake(data),
        )
        storage = await run_storage(extraction)
    except TimeoutError:
        raise HTTPException(status_code=502, detail="LLM 调用超时")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"流水线处理失败: {str(e)}")

    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info("流水线完成: case_id=%s, triplets=%d, %dms",
                data.case_id, len(extraction.triplets), elapsed_ms)

    return PipelineResponse(
        victim=storage.victim,
        suspect=storage.suspect,
        relations=storage.relations,
        transactions=storage.transactions,
        chat_history=storage.chat_history,
        deepfake_alert=storage.deepfake_alert,
        case_id=storage.case_id,
        elapsed_ms=elapsed_ms,
    )
