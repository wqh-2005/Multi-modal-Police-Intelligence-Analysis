"""KEO 公共配置。

本文件被 B 与 C 共同使用，包含两部分：

1. B 的文本 RAG 配置：路径、检索参数、回答模型、类别集合、划分名单读取。
2. C 的图谱 RAG 配置：``GraphRAGSettings``（pydantic，``KEO_`` 前缀环境变量）。

密钥一律从仓库根 .env 读取，本文件不存放任何密钥。

目录约定（由团队确定，勿擅改）：

    experiments/KEO/output/<版本>/         整理后的输入数据（原始交付保留在 guodu/）
        build/          B 建库原文、C 建库三元组与共用名单
        eval/           D 的评估原文与名单，不进入知识库
        splits/         划分名单（契约文件）
        questions/      D 的题库
        full/           A 的完整包（归档，B/C 默认建库不使用）
        manifest.json   导入登记与 SHA256
    experiments/KEO/output/index/          向量索引等派生存储
    experiments/KEO/output/results/        检索结果与回答（可随时清空重跑）
    experiments/KEO/output/test/           C 图谱实验的中间产物
    experiments/KEO/configs/config.py      唯一的路径与参数定义处

temp/ 是他人暂存交接目录，运行期一律不读，只由 core/ingest.py 拷入 data/。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# configs/config.py -> configs -> KEO -> experiments -> 仓库根
KEO_ROOT: Path = Path(__file__).resolve().parents[1]
REPO_ROOT: Path = Path(__file__).resolve().parents[3]
PROJECT_ROOT: Path = REPO_ROOT          # C 的代码沿用这个命名

load_dotenv(REPO_ROOT / ".env")

# ------------------------------------------------------------------ 输出根
OUTPUT_ROOT: Path = KEO_ROOT / "output"

# C 的图谱实验中间产物目录。原先 C 把自己的 output/test 也命名为 OUTPUT_ROOT，
# 与本文件 B 侧的 OUTPUT_ROOT 冲突，故改名为 GRAPH_TEST_ROOT，取值不变。
GRAPH_TEST_ROOT: Path = OUTPUT_ROOT / "test"

# ------------------------------------------------------------------ 数据集
# 本地目录版本默认 v1.0；A 的原始数据版本仍为 v1.1，见目录内 manifest.json。
DATASET_VERSION: str = os.getenv("KEO_DATASET_VERSION", "v1.0")
DATA_DIR: Path = OUTPUT_ROOT / DATASET_VERSION
DATA_MANIFEST: Path = DATA_DIR / "manifest.json"

# 导入源，仅 core/ingest.py 使用，其他模块不要引用
STAGING_ROOT: Path = Path(os.getenv("KEO_STAGING_ROOT", REPO_ROOT / "temp"))

# B 的输入
BUILD_CASES_PATH: Path = DATA_DIR / "build" / "cases_build.jsonl"
BUILD_TRIPLES_PATH: Path = DATA_DIR / "build" / "triples_build.jsonl"
BUILD_IDS_PATH: Path = DATA_DIR / "build" / "build_ids.txt"
DEV_IDS_PATH: Path = DATA_DIR / "build" / "dev_ids.txt"
EVAL_CASES_PATH: Path = DATA_DIR / "eval" / "cases_eval.jsonl"
EVAL_IDS_PATH: Path = DATA_DIR / "eval" / "eval_ids.txt"
SPLITS_PATH: Path = DATA_DIR / "splits" / "划分名单.json"

# D 的题库
QUESTION_ROOT: Path = DATA_DIR / "questions"
D_QUESTION_DEMO: Path = QUESTION_ROOT / "Demo版" / "questions.jsonl"
D_QUESTION_FULL: Path = QUESTION_ROOT / "全量版" / "questions_full.jsonl"
# D 组开发/测试集题库
D_QUESTION_DEV: Path = QUESTION_ROOT / "questions_dev.jsonl"
D_QUESTION_TEST: Path = QUESTION_ROOT / "questions_test.jsonl"
QUESTION_METADATA_DEV: Path = QUESTION_ROOT / "question_metadata_dev.jsonl"
QUESTION_METADATA_TEST: Path = QUESTION_ROOT / "question_metadata_test.jsonl"

# D 的金标（格式 1.5）。D 未交付原件，由 core/ingest_question_metadata.py 从
# A 的评估原文按题干前缀匹配反推：Demo 200/200、全量 32,859/32,859 全部命中，
# 但仍需 D 复核后才算正式底牌。
QUESTION_METADATA: Path = QUESTION_ROOT / "Demo版" / "question_metadata.jsonl"
QUESTION_METADATA_FULL: Path = QUESTION_ROOT / "全量版" / "question_metadata_full.jsonl"

# 完整包归档。B 默认读取 BUILD_CASES_PATH，C 默认读取 BUILD_TRIPLES_PATH。
FULL_DIR: Path = DATA_DIR / "full"
CASES_PATH: Path = FULL_DIR / "案例数据.json"
TRIPLES_PATH: Path = FULL_DIR / "三元组数据.jsonl"
ALIAS_PATH: Path = FULL_DIR / "实体别名映射.json"

INDEX_ROOT: Path = OUTPUT_ROOT / "index"            # 派生：向量库
RESULT_ROOT: Path = OUTPUT_ROOT / "results"         # 派生：检索与回答

SPLITS_FALLBACK: Path = KEO_ROOT / "configs" / "splits.json"


def resolve_splits_path(explicit: Path | None = None) -> Path:
    """B/C 共用正式名单；缺失时直接报错，不静默读取旧副本。"""
    if explicit:
        return Path(explicit)
    return SPLITS_PATH


def text_index_dir(debug: bool = False) -> Path:
    """文本索引目录。调试索引与正式索引物理隔离，避免混用。"""
    return INDEX_ROOT / ("text_index_debug" if debug else "text_index")


def text_collection(debug: bool = False) -> str:
    return "keo_text_debug" if debug else "keo_text"


def retrieval_path(debug: bool = False) -> Path:
    return RESULT_ROOT / ("retrieval_debug.jsonl" if debug else "retrieval.jsonl")


def answers_path(debug: bool = False) -> Path:
    return RESULT_ROOT / ("answers_debug.jsonl" if debug else "answers.jsonl")


# ------------------------------------------------------------------ 检索参数
EMBEDDING_MODEL: str = os.getenv("RAGENGING_MODEL", "BAAI/bge-m3")
TOP_K: int = int(os.getenv("KEO_TOP_K", "5"))
EMBED_BATCH: int = int(os.getenv("KEO_EMBED_BATCH", "32"))

# ------------------------------------------------------------------ 回答模型
# 三种方法必须共用同一模型与参数，否则 D 的评分差异无法归因
ANSWER_MODEL_ID: str = (
    os.getenv("KEO_ANSWER_MODEL")
    or os.getenv("LLMMODEL")
    or "Qwen/Qwen2.5-32B-Instruct"
)
ANSWER_TEMPERATURE: float = 0.0
ANSWER_MAX_TOKENS: int = int(os.getenv("KEO_ANSWER_MAX_TOKENS", "2048"))

# ------------------------------------------------------------------ API
API_KEY: str | None = os.getenv("SILICONFLOW_API_KEY")
BASE_URL: str = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "240"))
MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "3"))

# ------------------------------------------------------------------ 类别集合
# 与 案例数据.json 的「案件类别」取值完全一致，共 11 类
CATEGORIES: list[str] = [
    "刷单返利类",
    "冒充电商物流客服类",
    "虚假网络投资理财类",
    "虚假贷款类",
    "虚假征信类",
    "虚假购物服务类",
    "冒充公检法及政府机关类",
    "冒充领导熟人类",
    "其它类",
    "网络游戏产品虚假交易类",
    "网络婚恋、交友类",
]


def load_splits(path: Path | None = None) -> dict:
    """读取格式 1.1 的划分名单。

    文件由 A 产出，B/C/D 共用。缺失时抛出明确错误，不做静默兜底——
    静默兜底会让 B 与 C 用不同的建库范围，实验直接作废。
    """
    target = resolve_splits_path(path)
    if not target.exists():
        raise FileNotFoundError(
            f"划分名单不存在：{target}\n"
            "该文件由 A 产出（格式 1.1 §2.3），B/C/D 共用。\n"
            "A 交付前可用 build_index.py --debug-all-cases 做冒烟测试。"
        )
    with target.open(encoding="utf-8") as handle:
        splits = json.load(handle)

    for key in ("build_ids", "eval_ids"):
        if key not in splits:
            raise ValueError(f"划分名单缺少字段 {key}：{target}")

    overlap = set(splits["build_ids"]) & set(splits["eval_ids"])
    if overlap:
        raise ValueError(f"划分名单两侧重叠 {len(overlap)} 个案例，例如 {sorted(overlap)[:5]}")

    return splits


def require_api_key() -> str:
    """调用模型前校验密钥，避免跑到一半才报 401。"""
    if not API_KEY:
        raise RuntimeError(
            "未找到 SILICONFLOW_API_KEY。请确认仓库根 .env 存在且包含该变量。"
        )
    return API_KEY


# ================================================================== C 图谱 RAG
class GraphRAGSettings(BaseSettings):
    """图谱存储、节点向量和检索配置。

    环境变量统一使用 KEO_ 前缀。
    例如 NEO4J_URI 对应 KEO_NEO4J_URI。
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="KEO_",
        extra="ignore",
    )

    # ---------- 实验 Neo4j ----------

    # 必须明确配置实验实例，不回退到原业务连接。
    NEO4J_URI: str = Field(min_length=1)
    NEO4J_USER: str = Field(min_length=1)
    NEO4J_PASSWORD: str = Field(min_length=1, repr=False)

    # 独立实例可使用其默认数据库 neo4j。
    NEO4J_DATABASE: str = Field(default="neo4j", min_length=1)

    NEO4J_POOL_SIZE: int = Field(default=5, ge=1, le=20)
    NEO4J_CONNECTION_TIMEOUT: float = Field(default=10.0, gt=0)
    NEO4J_QUERY_TIMEOUT: float = Field(default=15.0, gt=0)

    # 每个写入事务处理的三元组数量。
    IMPORT_BATCH_SIZE: int = Field(default=300, ge=1, le=2000)

    # 删除来源、清理引用等操作的单批数量。
    DELETE_BATCH_SIZE: int = Field(default=300, ge=1, le=2000)

    # ---------- A 的数据 ----------

    CASES_PATH: Path = FULL_DIR / "案例数据.json"
    TRIPLES_PATH: Path = BUILD_TRIPLES_PATH
    SPLIT_PATH: Path = SPLITS_PATH

    # ---------- Chroma ----------

    CHROMA_PERSIST_DIR: Path = GRAPH_TEST_ROOT / "node_vectors"
    CHROMA_COLLECTION: str = Field(
        default="keo_graph_nodes",
        min_length=1,
    )

    # 当前版本统一使用余弦距离。
    CHROMA_DISTANCE: Literal["cosine"] = "cosine"

    # 单次从 Neo4j 读取的待同步节点数量。
    VECTOR_SYNC_BATCH_SIZE: int = Field(default=100, ge=1, le=1000)

    # ---------- Embedding ----------

    EMBEDDING_API_KEY: str = Field(min_length=1, repr=False)
    EMBEDDING_BASE_URL: str = Field(min_length=1)
    EMBEDDING_MODEL: str = Field(min_length=1)

    # 填写所选模型实际输出维度，后续索引类校验返回值。
    EMBEDDING_DIMENSION: int = Field(gt=0)

    # 单次 Embedding 请求包含的文本数量。
    EMBEDDING_BATCH_SIZE: int = Field(default=16, ge=1, le=128)

    # 修改节点描述生成方式时更新此版本。
    NODE_DESCRIPTION_VERSION: str = Field(
        default="v1",
        min_length=1,
    )

    # ---------- 查询规模 ----------

    SEED_TOP_K: int = Field(default=5, ge=1, le=20)

    # 一跳表示一条业务事实，不是 HEAD/TAIL 物理连接。
    MAX_HOPS: int = Field(default=2, ge=1, le=3)

    MAX_NEIGHBORS_PER_NODE: int = Field(default=20, ge=1, le=100)
    MAX_NODES: int = Field(default=100, ge=1, le=1000)
    MAX_EDGES: int = Field(default=150, ge=1, le=2000)

    # 只扩大每个前沿节点的有限候选池，不扩大返回子图上限。
    NEIGHBOR_CANDIDATE_FACTOR: int = Field(default=4, ge=1, le=10)
    MAX_EVIDENCE_CANDIDATES: int = Field(default=80, ge=1, le=500)
    EVIDENCE_PER_FACT: int = Field(default=2, ge=1, le=10)
    CONTEXT_MAX_CASES: int = Field(default=5, ge=1, le=20)
    CONTEXT_UNITS_PER_CASE: int = Field(default=2, ge=1, le=10)
    CONTEXT_FACTS_PER_CASE: int = Field(default=6, ge=1, le=30)
    FOREST_EXTRA_FACTS_PER_CASE: int = Field(default=2, ge=0, le=10)
    CONTEXT_TARGET_CHARS: int = Field(default=4000, ge=1, le=50000)

    # 证据全文召回与按案例扩展；可关闭用于消融，不影响节点向量。
    EVIDENCE_RECALL_ENABLED: bool = True
    EVIDENCE_FULLTEXT_INDEX: str = "keo_evidence_text"
    EVIDENCE_FULLTEXT_ANALYZER: str = "cjk"
    EVIDENCE_SEARCH_POOL: int = Field(default=120, ge=1, le=500)
    EVIDENCE_SEARCH_TOP_K: int = Field(default=24, ge=1, le=100)
    EVIDENCE_QUERY_MAX_TERMS: int = Field(default=64, ge=1, le=200)
    EVIDENCE_HIT_MAX_CHARS: int = Field(default=4000, ge=1, le=20000)
    EVIDENCE_FACTS_PER_HIT: int = Field(default=4, ge=1, le=20)
    RETRIEVAL_MAX_CASES: int = Field(default=5, ge=1, le=20)
    CASE_ANCHOR_FACTS: int = Field(default=3, ge=1, le=10)
    CASE_FACT_CANDIDATES: int = Field(default=12, ge=1, le=50)
    CASE_EXPANSION_ENABLED: bool = True

    MAX_EVIDENCE_UNITS: int = Field(default=20, ge=1, le=100)
    MAX_CONTEXT_CHARS: int = Field(default=8000, ge=1, le=50000)

    # ---------- 可选调试导出 ----------

    DEBUG_EXPORT_ENABLED: bool = False
    DEBUG_GRAPH_PATH: Path = GRAPH_TEST_ROOT / "graph.json"
    DEBUG_EXPORT_MAX_NODES: int = Field(default=200, ge=1, le=1000)
    DEBUG_EXPORT_MAX_EDGES: int = Field(default=300, ge=1, le=2000)


_GRAPH_RAG_CFG: GraphRAGSettings | None = None


def __getattr__(name: str):
    """按需实例化 C 的图谱配置。

    ``GraphRAGSettings`` 把 Neo4j 与 Embedding 字段设为必填。若在模块导入时就
    实例化，那么缺少 KEO_ 前缀环境变量时整个 ``configs.config`` 无法导入，
    B 的文本 RAG 会跟着一起失效。改为首次访问 ``graph_rag_cfg`` 时才实例化：

    - 对 C 仍是「缺配置就报错」，只是报错点从 import 移到第一次使用；
    - 对 B 不产生任何副作用。

    C 的代码写法 ``from experiments.KEO.configs.config import graph_rag_cfg``
    不需要改动，PEP 562 的模块级 __getattr__ 会接管。
    """
    global _GRAPH_RAG_CFG
    if name == "graph_rag_cfg":
        if _GRAPH_RAG_CFG is None:
            _GRAPH_RAG_CFG = GraphRAGSettings()
        return _GRAPH_RAG_CFG
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    print(f"仓库根      : {REPO_ROOT}")
    print(f"数据集版本  : {DATASET_VERSION}")
    print(f"数据目录    : {DATA_DIR}  (存在={DATA_DIR.exists()})")
    print(f"导入登记    : {DATA_MANIFEST}  (存在={DATA_MANIFEST.exists()})")
    print(f"建库原文    : {BUILD_CASES_PATH}  (存在={BUILD_CASES_PATH.exists()})")
    print(f"划分名单    : {SPLITS_PATH}  (存在={SPLITS_PATH.exists()})")
    print(f"Demo 题库   : {D_QUESTION_DEMO}  (存在={D_QUESTION_DEMO.exists()})")
    print(f"全量题库    : {D_QUESTION_FULL}  (存在={D_QUESTION_FULL.exists()})")
    print(f"索引目录    : {text_index_dir()}")
    print(f"结果目录    : {RESULT_ROOT}")
    print(f"C 实验目录  : {GRAPH_TEST_ROOT}")
    print(f"embedding   : {EMBEDDING_MODEL}")
    print(f"回答模型    : {ANSWER_MODEL_ID}  最高 {ANSWER_MAX_TOKENS} tokens")
    print(f"类别        : {len(CATEGORIES)} 类")
    print(f"API Key     : {'已配置' if API_KEY else '缺失'}")
