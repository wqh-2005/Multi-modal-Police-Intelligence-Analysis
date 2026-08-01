'''
最终输出
{
    "total": len(results),
    "results": [
        {
            "case_id": "...",
            "judgment": {
                "case_id":str,
                "is_fraud": true,
                "fraud_type": "冒充公检法类诈骗",
                "confidence": "高",
                "confidence_score": 0.95,
                "reason": "骗子冒充公检法机关，以涉嫌洗钱为由要求受害者转账到所谓的安全账户，符合冒充公检法类诈骗的典型特征。",
                "warning": "⚠️ 立即停止转账！公安机关不会通过电话办案，不会设立安全账户。请拨打 96110 报警。",
                "similar_cases": [
                    {
                        "content": "案例5：刘女士接到公安局电话，称其涉嫌洗钱，需将资金转入安全账户验证，被骗23万元。",
                        "score": 0.712
                    }
                ],
                "timestamp": "2026-07-27T14:35:00+08:00",
            },
            "alerts": [
                {
                    "type": "deepfake_warning",
                    "level": "高",
                    "title": "⚠️ 疑似使用AI换脸技术",
                    "message": "检测到视频内容可能使用了AI换脸技术...",
                    "actions": ["通过电话核实身份", "不要仅凭视频确认"]
                },
                {
                    "type": "fraud_warning",
                    "level": "高",
                    "title": "🔴 诈骗风险预警",
                    "message": "冒充公检法类诈骗...",
                    "actions": ["立即停止转账", "拨打96110"]
                },
                {
                    type="safe_notice",       # ← 值不同
                    level="低",               # ← 值不同
                    title="✅ 安全提示",       # ← 值不同
                    message="暂未发现风险...", # ← 值不同
                    actions=[...]        
                }

            ]
        }
    ]
}
'''

# app/core/alert/models.py
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from app.core.judgment.judger import Judger
from app.models.alerttmplates import AlertTemplate

class AlertOutput:

    def __init__(self):
        self.judger = Judger()

    def generate(self, neo4j_data_single: Dict) -> Dict:
        judger_result = self.judger.judge(neo4j_data_single)

        alerts: List[Alert] = []

        if not judger_result["deepfake_alert"]:
            alerts.append(AlertTemplate.deepfake_alert())

        if judger_result["is_fraud"]:
            alerts.append(AlertTemplate.fraud_alert(
                judger_result["fraud_type"], 
                judger_result["confidence"], 
                judger_result["warning"], 
                judger_result["reason"])
                )

        if not alerts:
            alerts.append(AlertTemplates.safe_alert())
        
        return {
            "case_id": judger_result["case_id"],
            "judgment": judger_result,
            "alerts": [alert.model_dump() for alert in alerts],
            "deepfake_detected": judger_result["deepfake_alert"]
        }
    def generator_batch(self, neo4j_data: List[Dict]) -> List[dict]:
        result = []
        for neo4j_data_single in neo4j_data:
            single_result  = self.generate(neo4j_data_single)
            result.append(single_result)

        return result
        

