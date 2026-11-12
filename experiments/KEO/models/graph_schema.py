"""图谱RAG的数据模型。"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#----------清洗后数据的输入-----------

class EntityInput(BaseModel):
    """三元组中原实体"""
    id: str = Field(min_length=1)
    name: str
    type: str = Field(min_length=1)
    role: Optional[str] = None

class EvidenceInput(BaseModel):
    """三元组中的原文证据"""

    model_config = ConfigDict(extra="allow")

    unit_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class TripleInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    triple_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    fraud_category: str = Field(min_length=1)

    head: EntityInput
    relation: str = Field(min_length=1)
    tail: EntityInput

    factuality: str = Field(min_length=1)
    evidence: List[EvidenceInput] = Field(min_length=1)

    confidence: Optional[float] = None
    status: Optional[str] = None


#=========节点映射结果==========

class NodeMapping(BaseModel):
    """
    NodeMapping返回图谱节点属性

    人员和其他局部节点直接使用原实体ID
    共享节点重新生成实体ID

    node_id: 图谱中最终实体的id
    graph_category：实体在图谱中的分类，由已有的 type 和 role 得到
    """

    node_id: str = Field(min_length=1)
    scope: Literal["case", "shared"]

    name: str
    type: str = Field(min_length=1)
    role: Optional[str] = None


    graph_category: str = Field(min_length=1)
    display_name: str


#========neo4j存储记录========

class OriginRecord(BaseModel):
    """对应KEOOrigin，保存原实体及其映射目标。对于共享节点，记录哪些原始节点映射

    entity_id直接使用A交付的实体id。
    node_id用于建立MAPS_TO关系，不生成新的映射编号。
    """

    entity_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)

    name: str
    type: str = Field(min_length=1)
    role: Optional[str] = None

    node_id: str = Field(min_length=1)


class EvidenceRecord(BaseModel):
    """
    使用（source_id，unit_id）作为复合唯一键
    unit_id：具体案例里面的哪一句
    """

    source_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    text: str = Field(min_length=1)

class FactRecord(BaseModel):
    """
    来源属性直接保存在事实中，不建立KEOCase。

    entity_id用于追溯映射前的原实体。
    node_id用于建立HEAD/TAIL连接，不重复写入事实属性。
    证据通过SUPPORTED_BY连接，不在事实中重复保存长文本。
    """
    triple_id: str = Field(min_length=1)

    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    fraud_category: str = Field(min_length=1)

    #原实体
    head_entity_id: str = Field(min_length=1)
    tail_entity_id: str = Field(min_length=1)

    head_node_id: str = Field(min_length=1)
    tail_node_id: str = Field(min_length=1)

    relation: str = Field(min_length=1)
    factuality: str = Field(min_length=1)

    confidence: Optional[float] = None
    status: Optional[str] = None


# ==================== 节点向量同步 ====================

class NodeVectorRecord(BaseModel):
    """准备写入Chroma的节点记录。

    Chroma记录ID直接使用node_id。
    向量由node_index计算，不在此处重复保存。
    """

    node_id: str = Field(min_length=1)
    document: str = Field(min_length=1)

    # 元数据只使用标量，空值由索引类省略。
    metadata: dict[str, str | int | float | bool] = Field(
        default_factory=dict
    )

    description_hash: str = Field(min_length=1)
    metadata_hash: str = Field(min_length=1)


class VectorSyncTask(BaseModel):
    """持久化的节点向量同步任务。

    按node_id唯一，不生成task_id。
    revision用于防止旧任务确认时清除更新后的任务。
    """

    node_id: str = Field(min_length=1)
    operation: Literal["upsert", "delete"]
    revision: int = Field(ge=1)


class KnowledgeBaseState(BaseModel):
    """独立实验库中的单份控制状态。

    version是内部更新版本，不是业务编号。
    embedding_fingerprint不包含密钥。
    """

    status: Literal["updating", "ready", "error"] = "updating"
    version: int = Field(default=0, ge=0)

    embedding_fingerprint: Optional[str] = None
    error_message: Optional[str] = None


# ==================== 查询得到的有限子图 ====================


class NodeSearchResult(BaseModel):
    """节点向量检索结果。

    score统一为余弦相似度，越高越相关。
    """

    node_id: str = Field(min_length=1)
    score: float = Field(allow_inf_nan=False)


class GraphEdge(BaseModel):
    """从KEOFact恢复的一条逻辑业务边。

    仅用于有限查询子图，不重复保存完整实体和长证据。
    来源类型、案件类别按需从KEOFact另外读取。
    """

    triple_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)

    head_node_id: str = Field(min_length=1)
    tail_node_id: str = Field(min_length=1)

    relation: str = Field(min_length=1)
    factuality: str = Field(min_length=1)

    confidence: Optional[float] = None
    status: Optional[str] = None


class GraphSubgraph(BaseModel):
    """本次检索得到的有限逻辑子图。

    节点数、边数等限制由graph_retriever执行。
    """

    nodes: List[NodeMapping] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)


class GraphRAGQuestion(BaseModel):
    """C对外接收的格式1.2，只读取问题本身。"""

    model_config = ConfigDict(extra="ignore")

    question_id: str = Field(min_length=1, strict=True)
    question_type: Literal["global_analysis", "case_judgment"]
    question: str = Field(min_length=1, strict=True)


class RetrievedGraphEvidence(BaseModel):
    """上下文实际采用的原文证据，字段兼容格式1.3。"""

    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    triple_ids: List[str] = Field(min_length=1)


class ContextBuildResult(BaseModel):
    """内部上下文结果；耗时只统计上下文阶段，不重复累计检索耗时。"""

    context: str = ""
    evidence: List[RetrievedGraphEvidence] = Field(default_factory=list)
    selected_triple_ids: List[str] = Field(default_factory=list)
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)


class GraphRetrievalResult(BaseModel):
    """内部子图检索结果，保留上下文重建所需的版本和索引身份。

    不是对外格式1.3；最终context和evidence由后续模块生成。
    """

    seeds: List[NodeSearchResult] = Field(default_factory=list)
    subgraph: GraphSubgraph = Field(default_factory=GraphSubgraph)
    version: int = Field(ge=0)
    fingerprint: str = Field(min_length=1)
    index_key: str = Field(min_length=1)
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)


# ==================== 暂时兼容旧版构图代码 ====================


class EdgeAttributes(BaseModel):
    """旧版grapy_builder使用的边属性。

    暂时保留，避免影响尚未重写的旧代码。
    新存储类不将嵌套evidence写入Neo4j属性。
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    factuality: str = Field(min_length=1)

    evidence: List[EvidenceInput] = Field(min_length=1)

    confidence: Optional[float] = None
    status: Optional[str] = None
