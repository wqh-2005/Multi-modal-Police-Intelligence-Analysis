"""知识抽取与知识图谱数据模型。

严格遵循 docs/数据结构文档.pdf 定义的格式。

格式 1.1（用户输入）:
    前端构建，由 BatchMultimodalRequest 承载，供模块一消费。

格式 1.2（多模态识别输出 → 知识抽取输入）:
    模块一产出，由 BatchMultimodalResponse 承载，供模块二消费。

格式 1.3（知识抽取输出）:
    模块二产出，由 ExtractionOutput 承载，供模块三消费。

格式 1.4（知识存储输出）:
    模块三产出，由 GraphStorageOutput 承载，供模块四消费。
"""
from typing import Optional, List, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 格式 1.1：用户输入（前端 → 模块一）
# ---------------------------------------------------------------------------

class InputItem(BaseModel):
    """单条用户输入。

    Attributes:
        type: 输入类型（text/image/audio/video）。
        content: 识别后的文本内容。
        timestamp: 带时区信息的时间戳。
        file_path: 后端生成的本地存储路径。
        file_name: 后端生成的文件名。
    """
    type: Literal["text", "image", "audio", "video"]
    content: Optional[str] = None
    timestamp: Optional[str] = None
    file_path: Optional[str] = None
    file_name: Optional[str] = None


class BatchMultimodalRequest(BaseModel):
    """用户输入请求（格式 1.1）。

    Attributes:
        case_id: 案件编号。
        inputs: 多模态输入列表。
    """
    case_id: str
    inputs: List[InputItem]


# ---------------------------------------------------------------------------
# 格式 1.2：多模态识别输出（模块一 → 模块二）
# ---------------------------------------------------------------------------

class OutputItem(BaseModel):
    """模块一（多模态识别）输出的单条结果。

    Attributes:
        text: 识别出的文本内容，默认 "此文本为空"。
        type: 模态类型（text/image/audio/video）。
        deepfake_result: 是否检测到 AI 换脸/伪造，None 表示未检测。
        confidence: 识别置信度 [0, 1]，None 表示不可用。
        status: 处理状态（done/error，见格式 1.2 规范）。
        processing_time_ms: 处理耗时（毫秒）。
    """
    text: Optional[str] = "此文本为空"
    type: Literal["text", "image", "audio", "video"]
    deepfake_result: Optional[bool] = None
    confidence: Optional[float] = None
    status: str = "pending"
    processing_time_ms: int = 0


class BatchMultimodalResponse(BaseModel):
    """模块一传递给模块二的知识抽取输入（格式 1.2）。

    Attributes:
        case_id: 案件编号。
        outputs: 多模态识别结果列表。
    """
    case_id: str
    outputs: List[OutputItem]

class Triplet(BaseModel):
    """三元组（格式 1.3，triplets 数组元素）。

    数据结构文档规定三元组仅包含三个字段：subject / relation / object。
    subject_type / object_type 为可选字段，由 LLM 在抽取时附带，
    供存储模块做实体类型推断用，不对外暴露。

    Attributes:
        subject: 头实体名称。
        relation: 关系类型（冒充、转账、要求等）。
        object: 尾实体名称。
        subject_type: 头实体类型（可选，LLM 推断）。
        object_type: 尾实体类型（可选，LLM 推断）。
    """
    subject: str
    relation: str
    object: str
    subject_type: Optional[str] = None
    object_type: Optional[str] = None


class ExtractionOutput(BaseModel):
    """知识抽取输出（格式 1.3）。

    模块二（extraction_service）产出，传递给模块三（storage_service）。

    Attributes:
        triplets: 三元组列表。
        raw_text: 原始输入文本，供下游还原上下文。
        extraction_confidence: 整体抽取置信度（0-1）。
        case_id: 案件编号，贯穿全流程的唯一标识。
        deepfake_alert: 多模态识别模块是否检测到 AI 换脸/伪造。
    """
    triplets: List[Triplet]
    raw_text: str
    extraction_confidence: float
    case_id: str = ""
    deepfake_alert: bool = False


# ---------------------------------------------------------------------------
# 格式 1.4：知识存储输出
# ---------------------------------------------------------------------------

class Victim(BaseModel):
    """受害者信息（格式 1.4）。

    Attributes:
        name: 姓名，默认 "未知"。
        age: 年龄，默认 0。
        profession: 职业，默认 "未知"。
        phone: 手机号，默认 None。
        id_card: 身份证号，默认 None。
    """
    name: str = "未知"
    age: int = 0
    profession: str = "未知"
    phone: Optional[str] = None
    id_card: Optional[str] = None


class Suspect(BaseModel):
    """嫌疑人信息（格式 1.4）。

    Attributes:
        name: 姓名，默认 "未知"。
        phone: 手机号，默认 None。
        account: 银行账户，默认 None。
    """
    name: str = "未知"
    phone: Optional[str] = None
    account: Optional[str] = None


class Relation(BaseModel):
    """实体关系（格式 1.4，relations 数组元素）。

    字段名遵循数据结构文档：from / type / to。
    内部用 from_entity 避免与 Python 关键字冲突，序列化时别名为 from。

    Attributes:
        from_entity: 来源实体名称（别名 from）。
        type: 关系类型。
        to_entity: 目标实体名称（别名 to）。
    """
    model_config = {"populate_by_name": True}

    from_entity: str = Field(alias="from")
    type: str
    to_entity: str = Field(alias="to")


class Transaction(BaseModel):
    """资金交易记录（格式 1.4，transactions 数组元素）。

    内部用 from_entity 避免与 Python 关键字冲突，序列化时别名为 from。

    Attributes:
        from_entity: 转出方（别名 from）。
        to_entity: 转入方（别名 to）。
        amount: 转账金额（元）。
        time: 交易时间（ISO 8601），默认 None。
    """
    model_config = {"populate_by_name": True}

    from_entity: str = Field(alias="from")
    to_entity: str = Field(alias="to")
    amount: float
    time: Optional[str] = None


class GraphStorageOutput(BaseModel):
    """知识存储输出（格式 1.4）。

    模块三（storage_service）产出，传递给模块四（智能研判）。

    Attributes:
        victim: 受害者信息。
        suspect: 嫌疑人信息。
        relations: 实体关系列表。
        transactions: 资金交易记录列表。
        chat_history: 原始聊天记录（截断至 500 字）。
        deepfake_alert: 是否触发 AI 换脸警告。
        case_id: 案件编号。
    """
    victim: Victim
    suspect: Suspect
    relations: List[Relation]
    transactions: List[Transaction]
    chat_history: str
    deepfake_alert: bool = False
    case_id: str = ""
