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
from app.core.knowledge.storage_service import (
    delete_case_history,
    get_case_history,
    list_case_history,
    run_storage,
    update_case_judgment,
    _get_driver,
)
from neo4j.exceptions import DriverError
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

app.add_middleware(
    CORSMiddleware,
    # 仅允许前端开发地址；上线后请替换为实际部署域名（勿用 "*" 暴露历史数据接口）
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],          # 必须为 "*" 才会自动处理 OPTIONS
    allow_headers=["*"],
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

def _has_meaningful_data(s: str) -> bool:
        s = (s or "").strip()
        if not s:
            return False
        SENTIELS = {
            "未识别到文本",      
            "识别错误",         
        }

        if s in SENTIELS:
            return False
        if s.startswith("识别中出错"):
            return False
        return True

def _merge_texts(multimodal_result: BatchMultimodalResponse) -> str:
    """从多模态输出中合并所有有效文本。"""
    parts = []
    for item in multimodal_result.outputs:
        if item.status in ("done", "success") and _has_meaningful_data(item.text):
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
        logger.exception("多模态识别失败: case_id=%s", data.case_id)
        raise HTTPException(status_code=500, detail="多模态识别失败，请检查输入文件格式后重试")
    stages["multimodal_ms"] = round((time.time() - t1) * 1000)

    merged_text = _merge_texts(multimodal_result)
    deepfake_alert = _check_deepfake(multimodal_result)

    if not merged_text:
        has_video = any(item.type == "video" for item in data.inputs)
        if has_video:
            detail = (
                "视频文件未识别出可分析的文字内容：当前视频仅支持 AI 换脸检测，"
                "请搭配文本描述或音频一起上传，或改用其他格式"
            )
        else:
            detail = "所有模态输出文本均为空，无法继续"
        raise HTTPException(status_code=400, detail=detail)

    # ── 阶段 2：知识抽取 ──
    t2 = time.time()
    try:
        # 使用多模态阶段返回的 case_id（原始编号 + 16 位随机后缀），
        # 使同一案件编号的每次提交在抽取/存储/历史链路中互不覆盖
        extraction = await run_extraction(
            merged_text,
            case_id=multimodal_result.case_id,
            deepfake_alert=deepfake_alert,
        )
    except TimeoutError:
        raise HTTPException(status_code=502, detail="LLM 调用超时")
    except Exception as e:
        logger.exception("知识抽取失败: case_id=%s", data.case_id)
        raise HTTPException(status_code=500, detail="知识抽取失败，请稍后重试")
    stages["extraction_ms"] = round((time.time() - t2) * 1000)

    # ── 阶段 3：知识图谱存储 ──
    t3 = time.time()
    try:
        storage = await run_storage(extraction)
    except Exception as e:
        err_msg = str(e).lower()
        if "couldn't connect" in err_msg or "service unavailable" in err_msg:
            raise HTTPException(status_code=502, detail="图数据库 Neo4j 不可达")
        logger.exception("知识存储失败: case_id=%s", data.case_id)
        raise HTTPException(status_code=500, detail="知识存储失败，请稍后重试")
    stages["storage_ms"] = round((time.time() - t3) * 1000)

    # ── 阶段 4：智能研判 ──
    t4 = time.time()
    _init_knowledge_base()
    neo4j_dict = storage.model_dump(by_alias=True)
    try:
        result = await AlertOutput().generate(neo4j_dict)
    except Exception as e:
        logger.exception("研判失败: case_id=%s", data.case_id)
        raise HTTPException(status_code=500, detail="研判失败，请稍后重试")
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

    print("====================================================================")
    print(f"多模态识别内容：{merged_text}")
    print("====================================================================")
    print(f"知识抽取结果：{extraction}")
    print("====================================================================")
    print(f"知识图谱存储结果：{storage}")
    print("====================================================================")
    print(f"智能研判结果：{result}")
    print("====================================================================")

    # 将研判结果写入案件记录节点（供历史记录详情展示；失败不阻断流水线）
    try:
        driver = _get_driver()
        await update_case_judgment(driver, case_id=multimodal_result.case_id, judgment=result["judgment"])
    except Exception as e:
        logger.warning("研判结果写入案件记录失败: %s", e)

    return PipelineResponse(
        case_id=result["case_id"],
        judgment=result["judgment"],
        alerts=result["alerts"],
        deepfake_detected=result["deepfake_detected"],
        elapsed_ms=total_ms,
        stages=stages,
    )


@app.get("/api/v1/history", tags=["历史记录"])
async def list_history(limit: int = 50):
    """查询最近分析的历史案件记录（按创建时间倒序）。

    案件记录由流水线的知识存储阶段写入 Neo4j（:Case 节点）。
    Neo4j 不可达时返回空列表（不阻断页面）。
    """
    try:
        # 限制查询条数，避免 limit 异常值（负数/0/超大）导致报错或拉全表
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50
    try:
        driver = _get_driver()
        await driver.verify_connectivity()
        cases = await list_case_history(driver, limit=limit)
        return {"cases": cases, "total": len(cases)}
    except DriverError as e:
        # Neo4j 不可达/认证失败（ServiceUnavailable/AuthError 等）：返回空列表，不阻断页面
        logger.warning("历史记录查询失败(Neo4j): %s", e, exc_info=True)
        return {"cases": [], "total": 0}


@app.get("/api/v1/history/{case_id}", tags=["历史记录"])
async def get_history_detail(case_id: str):
    """查询单个历史案件的完整记录（含研判结果）。

    案件不存在返回 404；Neo4j 不可达时返回 503。
    """
    try:
        driver = _get_driver()
        await driver.verify_connectivity()
        case = await get_case_history(driver, case_id=case_id)
    except DriverError as e:
        logger.warning("历史详情查询失败(Neo4j): %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail="图数据库 Neo4j 不可达")
    if case is None:
        raise HTTPException(status_code=404, detail=f"案件 {case_id} 不存在")
    return case


@app.delete("/api/v1/history/{case_id}", tags=["历史记录"])
async def delete_history_case(case_id: str):
    """删除指定历史案件记录（仅删除 :Case 记录节点，不级联删除共享实体）。

    case_id 为流水线生成的唯一编号（原始案件名 + 16 位后缀）。
    案件不存在返回 404；Neo4j 不可达时返回 503。
    """
    try:
        driver = _get_driver()
        await driver.verify_connectivity()
        deleted = await delete_case_history(driver, case_id=case_id)
    except DriverError as e:
        logger.warning("历史删除失败(Neo4j): %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail="图数据库 Neo4j 不可达")
    if not deleted:
        raise HTTPException(status_code=404, detail=f"案件 {case_id} 不存在")
    return {"deleted": True, "case_id": case_id}
