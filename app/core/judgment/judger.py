# Judger.py
import os
import sys
from typing import Dict, Optional
from pathlib import Path
from typing import Optional
from pprint import pprint
import asyncio

from app.core.judgment.ragengine import RagEngine
from app.core.judgment.llmclient import LLMClient
from datetime import datetime
from app.config import rag_cfg, com_cfg
from app.models.output.judgeroutput import JudgmentResult, JudgmentError
from app.models.input.neo4j_input import Neo4jData

class Judger:

    _global_engine : Optional[RagEngine] = None
    _global_kb_loaded: bool = False

    def __init__(self):
        self.engine = Judger._global_engine
        self.llm_client = LLMClient()
        self.top_k = com_cfg.RAG_TOP_K
    @classmethod    
    def init_knowledge_base(cls):
        if cls._global_kb_loaded:
            return 0
        if cls._global_engine is None:
            cls._global_engine = RagEngine(rag_cfg.JSON_PROCESSED_DIR, rag_cfg.RAG_COLLECTION)
        cnt = cls._global_engine.load_from_json(rag_cfg.EXAMPLE_JSON_Dir)
        cls._global_kb_loaded = True
        return cnt

    def _has_meaningful_data(self, s: str) -> bool:
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


    async def judge(self, neo4j_data: dict) -> dict:
        # 主方法：执行完整研判流程

        try:
            graph_data = Neo4jData(**neo4j_data)
        except Exception as e:
            print(f"❌ Neo4j数据格式错误: {str(e)}")
            # 使用默认值创建
            graph_data = Neo4jData(
                case_id=neo4j_data.get("case_id", "unknown"),
                chat_history=neo4j_data.get("chat_history", ""),
                deepfake_alert=neo4j_data.get("deepfake_alert", False)
            )

            
        victim_text = graph_data.to_victim_text()

        if not self._has_meaningful_data(graph_data.chat_history) and not graph_data.relations and not graph_data.transactions:
            print(f"案例 {graph_data.case_id} 无有效研判信息，跳过LLM调用")
            return JudgmentResult.fallback(
                case_id = neo4j_data.get("case_id"),
                error_msg="无可分析内容，无法进行有效研判",
                error_source="judger",
            ).model_dump()

        try:
            docs = await self.engine.search(victim_text)
        
        except Exception as e:
            print(f"RAG检索失败：{str(e)}")
            return JudgmentResult.fallback(
                case_id=neo4j_data.get("case_id", "unknown"),
                error_msg=str(e),
                error_source="rag_engine"
            ).model_dump() 

        similar_info = []
        for doc in docs:
            res = {
                "content": doc["content"],
                "fraud_type":doc["metadata"]["fraud_type"],
                "score": round(doc["score"], 3)
            }
            
            similar_info.append(res)

        try:
            result =  (await self.llm_client.judge(victim_text, similar_info)).model_dump() 

        except Exception as e:
            print("调用大模型失败")
            return JudgmentResult.fallback(
                case_id=neo4j_data.get("case_id", "unknown"),
                error_msg=str(e),
                error_source="llm_client"
            ).model_dump()

        return JudgmentResult(
            case_id=neo4j_data.get("case_id","unknown"),
            is_fraud=result.get("is_fraud", False),
            fraud_type=result.get("fraud_type", ""),
            confidence=result.get("confidence", "低"),
            confidence_score=result.get("confidence_score", 0.0),
            reason=result.get("reason", "无法判断"),
            warning=result.get("warning", "请人工复核"),
            deepfake_alert=neo4j_data.get("deepfake_alert", False),
            similar_cases=similar_info,
            timestamp=datetime.now().isoformat()
        ).model_dump()

if __name__ == "__main__":
    pass