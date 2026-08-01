'''
最终输出格式：
{
    "case_id":str,
    "is_fraud": true,
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

# app/models/judgment_models.py
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime

class SimilarCase(BaseModel):
    """相似案例"""
    content: str = Field(description="案例描述内容")
    fraud_type: str = Field(description="相似诈骗案例的诈骗")
    score: float = Field(ge=0, le=1, description="相似度分数，0-1之间")
    

class JudgmentResult(BaseModel):
    """研判结果（格式1.5）"""
    case_id: str = Field(description="案例唯一标识")
    is_fraud: bool = Field(description="是否遭受诈骗")
    fraud_type: str = Field(description="诈骗类型，非诈骗时为空字符串")
    confidence: str = Field(description="置信度：高/中/低")
    confidence_score: float = Field(ge=0, le=1, description="置信度分数，0-1之间")
    reason: str = Field(description="研判理由")
    warning: str = Field(description="预警建议")
    deepfake_alert: bool = Field(default=False, description="是否检测到AI换脸")
    similar_cases: List[SimilarCase] = Field(default=[], description="相似案例列表")
    timestamp: str = Field(description="研判完成时间（ISO格式）")


def create_fallback_result(case_id: str, error_msg: str = "研判服务暂时不可用") -> dict:
    """创建降级结果（LLM 调用失败时使用）"""
    return {
        "case_id": case_id,
        "is_fraud": False,
        "fraud_type": "",
        "confidence": "低",
        "confidence_score": 0.0,
        "reason": f"研判服务暂时不可用，请人工复核。错误信息：{error_msg}",
        "warning": "请通过96110反诈专线进行人工咨询",
        "deepfake_alert": False,
        "similar_cases": [],
        "timestamp": datetime.now().isoformat()
    }