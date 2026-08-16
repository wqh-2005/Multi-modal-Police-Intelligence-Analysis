from pydantic import BaseModel, Field
from typing import Annotated
from typing import Optional

class FraudJudgment(BaseModel):
    """诈骗研判结果"""
    #Field:给字段添加描述
    is_fraud: bool = Field(description="是否遭受诈骗")
    fraud_type: str = Field(description="诈骗类型，非诈骗时为空")
    confidence: str = Field(description="置信度：高/中/低")
    confidence_score: Annotated[float, Field(ge=0, le=1, description="对 is_fraud 结论的确定程度（0~1，越高越确定），不是诈骗概率")]
    reason: str = Field(description="研判理由")
    warning: str = Field(description="预警建议")