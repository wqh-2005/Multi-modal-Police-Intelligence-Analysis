from typing import List, Optional
from pydantic import BaseModel, Field

class Alert(BaseModel):
    type: str = Field(description="预警类型：deepfake_alert/fraud_warning/safe_notice")
    level: str = Field(description="严重程度：高/中/低")
    title: str = Field(description="预警标题（用户第一眼看到）")
    message: str = Field(description="预警核心描述")
    warning: Optional[str] = Field(default=None, description="紧急建议（复用 Judger 的 warning）")
    reason: Optional[str] = Field(default=None, description="判断理由（复用 Judger 的 reason）")