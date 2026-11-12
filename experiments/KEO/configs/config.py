"""C图谱RAG配置。"""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# config.py → configs → KEO → experiments → 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]
KEO_ROOT = PROJECT_ROOT / "experiments" / "KEO"
OUTPUT_ROOT = KEO_ROOT / "output" / "test"


class GraphRAGSettings(BaseSettings):
    """图谱存储、节点向量和检索配置。

    环境变量统一使用KEO_前缀。
    例如NEO4J_URI对应KEO_NEO4J_URI。
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="KEO_",
        extra="ignore",
    )

    # ---------- 实验Neo4j ----------

    # 必须明确配置实验实例，不回退到原业务连接。
    NEO4J_URI: str = Field(min_length=1)
    NEO4J_USER: str = Field(min_length=1)
    NEO4J_PASSWORD: str = Field(min_length=1, repr=False)

    # 独立实例可使用其默认数据库neo4j。
    NEO4J_DATABASE: str = Field(default="neo4j", min_length=1)

    NEO4J_POOL_SIZE: int = Field(default=5, ge=1, le=20)
    NEO4J_CONNECTION_TIMEOUT: float = Field(default=10.0, gt=0)
    NEO4J_QUERY_TIMEOUT: float = Field(default=15.0, gt=0)

    # 每个写入事务处理的三元组数量。
    IMPORT_BATCH_SIZE: int = Field(default=300, ge=1, le=2000)

    # 删除来源、清理引用等操作的单批数量。
    DELETE_BATCH_SIZE: int = Field(default=300, ge=1, le=2000)

    # ---------- A的数据 ----------

    CASES_PATH: Path = OUTPUT_ROOT / "案例数据.json"
    TRIPLES_PATH: Path = OUTPUT_ROOT / "三元组数据.jsonl"
    SPLIT_PATH: Path = OUTPUT_ROOT / "划分名单.json"

    # ---------- Chroma ----------

    CHROMA_PERSIST_DIR: Path = OUTPUT_ROOT / "node_vectors"
    CHROMA_COLLECTION: str = Field(
        default="keo_graph_nodes",
        min_length=1,
    )

    # 当前版本统一使用余弦距离。
    CHROMA_DISTANCE: Literal["cosine"] = "cosine"

    # 单次从Neo4j读取的待同步节点数量。
    VECTOR_SYNC_BATCH_SIZE: int = Field(default=100, ge=1, le=1000)

    # ---------- Embedding ----------

    EMBEDDING_API_KEY: str = Field(min_length=1, repr=False)
    EMBEDDING_BASE_URL: str = Field(min_length=1)
    EMBEDDING_MODEL: str = Field(min_length=1)

    # 填写所选模型实际输出维度，后续索引类校验返回值。
    EMBEDDING_DIMENSION: int = Field(gt=0)

    # 单次Embedding请求包含的文本数量。
    EMBEDDING_BATCH_SIZE: int = Field(default=16, ge=1, le=128)

    # 修改节点描述生成方式时更新此版本。
    NODE_DESCRIPTION_VERSION: str = Field(
        default="v1",
        min_length=1,
    )

    # ---------- 查询规模 ----------

    SEED_TOP_K: int = Field(default=5, ge=1, le=20)

    # 一跳表示一条业务事实，不是HEAD/TAIL物理连接。
    MAX_HOPS: int = Field(default=2, ge=1, le=3)

    MAX_NEIGHBORS_PER_NODE: int = Field(default=20, ge=1, le=100)
    MAX_NODES: int = Field(default=100, ge=1, le=1000)
    MAX_EDGES: int = Field(default=150, ge=1, le=2000)

    MAX_EVIDENCE_UNITS: int = Field(default=20, ge=1, le=100)
    MAX_CONTEXT_CHARS: int = Field(default=8000, ge=1, le=50000)

    # ---------- 可选调试导出 ----------

    DEBUG_EXPORT_ENABLED: bool = False
    DEBUG_GRAPH_PATH: Path = OUTPUT_ROOT / "graph.json"
    DEBUG_EXPORT_MAX_NODES: int = Field(default=200, ge=1, le=1000)
    DEBUG_EXPORT_MAX_EDGES: int = Field(default=300, ge=1, le=2000)


# 与现有项目一样，通过模块级实例统一读取。
graph_rag_cfg = GraphRAGSettings()