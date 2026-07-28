from pydantic import BaseModel, Field
from typing import Optional, Literal

# 文档格式1.1, 用户输入
class MultimodalRequest(BaseModel):
    # 把输入类型约束为列表的几个之一
    input_type: Literal["text", "image", "audio", "video"]
    # file_path需要是str，否则必须为空
    file_path: Optional[str] = None
    # 带时区信息
    timestamp: Optional[str] = None
# 文档格式1.2, 多模态输出
class MultimodalResponse(BaseModel):
    test:str = "此文本为空"
    source_type: Literal["text", "image", "audio", "video"]
    deepfake_result: bool = False
    confidence: float = 0
    processing_time_ms: int = 0