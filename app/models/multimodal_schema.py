from pydantic import BaseModel, Field
from typing import Optional, Literal, List

# 文档格式1.1, 用户输入
class InputItem(BaseModel):
    # 把输入类型约束为列表的几个之一
    type: Literal["text", "image", "audio", "video"]
    # 识别成了文字后放到该字段
    content: str
    # 带时区信息
    timestamp: Optional[str] = None
    # 由后端生成，存储在本地
    file_path: Optional[str] = None
    file_name: Optional[str] = None
# 文档格式1.1, 用户输入
class BatchMultimodalRequest(BaseModel):
    case_id: str
    inputs: List[InputItem]

# 文档格式1.2, 多模态输出
class OutputItem(BaseModel):
    text: Optional[str] = "此文本为空"
    type: Literal["text", "image", "audio", "video"]
    deepfake_result: Optional[bool] = None
    confidence: Optional[float] = None
    status: str = "pending"
class BatchMultimodalResponse(BaseModel):
    case_id: str
    processing_time_ms: int = 0
    outputs: List[OutputItem]