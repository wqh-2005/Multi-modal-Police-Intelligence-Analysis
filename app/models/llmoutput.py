from pydantic import BaseModel, Field
from typing import Annotated
from typing import Optional

class FraudJudgment(BaseModel):
    """诈骗研判结果"""
    #Field:给字段添加描述
    is_fraud: str = Field(description="是否遭受诈骗：是/否")
    fraud_type: Optional[str] = Field(
        default="",
        description="诈骗类型，非诈骗时为空"
    )
    confidence: str = Field(description="置信度：高/中/低")
    confidence_score: Annotated[float, Field(ge=0, le=1, description="0~1浮点置信分数，数值越高研判可信度越高")]
    reason: str = Field(description="研判理由")
    warning: str = Field(description="预警建议")