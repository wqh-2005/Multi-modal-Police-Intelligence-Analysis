# Judger.py
import os
import sys
from typing import Dict, Optional
from pathlib import Path
from typing import Optional
from pprint import pprint

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

    def judge(self, neo4j_data: dict) -> dict:
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

        if not graph_data.chat_history and not graph_data.relations and not graph_data.transactions:
            print(f"案例 {graph_data.case_id} 无有效研判信息，跳过LLM调用")
            return JudgmentResult.fallback(
                case_id = neo4j_data.get("case_id"),
                error_msg="聊天记录与知识图谱均为空，无法进行有效研判",
                error_source="judger",
            ).model_dump()

        try:
            docs = self.engine.search(victim_text)
        
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
            result = self.llm_client.judge(victim_text, similar_info).model_dump() 

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


        '''
        最终输出格式：
        {
            "case_id":str,
            "is_fderaud": true,
            "fraud_type": "冒充公检法类诈骗",
            "confidence": "高",
            "confidence_score": 0.95,
            "reason": "骗子冒充公检法机关，以涉嫌洗钱为由要求受害者转账到所谓的安全账户，符合冒充公检法类诈骗的典型特征。",
            "warning": "⚠️ 立即停止转账！公安机关不会通过电话办案，不会设立安全账户。请拨打 96110 报警。",
            "deepfake_alert": false,
            "similar_cases": [
                {
                    "content": "案例5：刘女士接到公安局电话，称其涉嫌洗钱，需将资金转入安全账户验证，被骗23万元。",
                    "score": 0.712
                }
            ],
            "timestamp": "2026-07-27T14:35:00+08:00",
        }
        '''

if __name__ == "__main__":
    # 1. 程序启动先全局加载知识库（只执行一次）
    load_count = Judger.init_knowledge_base()
    print(f"知识库加载完成，新增案例数：{load_count}\n")

    # 2. 模拟上游Neo4j输出的图谱字典
    test_neo4j_data = {
        "case_id": "TEST-001",
        "victim": {
            "name": "张先生",
            "age": 45,
            "profession": "公司职员"
        },
        "relations": [
            {
                "from": "010-XXXXXXX陌生来电",
                "type": "通话",
                "to": "张先生"
            }
        ],
        "transactions": [
            {
                "from": "张先生银行卡",
                "to": "所谓安全账户",
                "amount": 380000
            }
        ],
        "chat_history": "对方自称北京朝阳公安，称本人身份证被盗用洗钱200万，要求全部资金转入安全账户配合调查，否则追究刑责，受害人准备转账前拨打96110咨询。",
        "deepfake_alert": False,
    }

    # 3. 实例化Judger
    judger = Judger()
    # 4. 执行研判
    res = judger.judge(neo4j_data=test_neo4j_data)

    # 5. 格式化打印完整结果
    from pprint import pprint
    print("=======诈骗研判完整输出结果=======")
    pprint(res, width=130)