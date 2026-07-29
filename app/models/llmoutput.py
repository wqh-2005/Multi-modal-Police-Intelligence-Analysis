from pydantic import BaseModel, Field
class FraudJudgment(BaseModel):
    """诈骗研判结果"""
    #Field:给字段添加描述
    is_fraud: str = Field(description="是否遭受诈骗：是/否")
    fraud_type: str = Field(description="诈骗类型")
    confidence: str = Field(description="置信度：高/中/低")
    reason: str = Field(description="研判理由")
    warning: str = Field(description="预警建议")