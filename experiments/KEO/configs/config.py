"""KEO 公共配置：路径、参数与类别集合。
密钥只从仓库根 .env 读取，本文件不存放任何密钥。
目录约定（由团队确定，勿擅改）：
    experiments/KEO/data/<版本>/           输入数据（由 core/ingest.py 导入，只读）
        build/          建库原文与名单
        splits/         划分名单（契约文件）
        questions/      D 的题库
        full/           完整包（可选，B 不使用）
        manifest.json   导入登记与 SHA256
    experiments/KEO/output/index/          向量索引等派生存储
    experiments/KEO/output/results/        检索结果与回答（可随时清空重跑）
    experiments/KEO/configs/config.py      唯一的路径与参数定义处

temp/ 是他人暂存交接目录，运行期一律不读，只由 core/ingest.py 拷入 data/。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# experiments/KEO/configs/config.py -> configs -> KEO -> experiments -> 仓库根
KEO_ROOT: Path = Path(__file__).resolve().parents[1]
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

load_dotenv(REPO_ROOT / ".env")

# ------------------------------------------------------------------ 路径
OUTPUT_ROOT: Path = KEO_ROOT / "output"

# ------------------------------------------------------------------ 数据集
# 运行期不读 temp/：先由 core/ingest.py 导入 data/<版本>/ 并登记 SHA256，
# 之后所有模块只读这里。data/ 已被 .gitignore 覆盖（第 36 行 data/），不入库。
DATASET_VERSION: str = os.getenv("KEO_DATASET_VERSION", "v1.1")
DATA_DIR: Path = KEO_ROOT / "data" / DATASET_VERSION
DATA_MANIFEST: Path = DATA_DIR / "manifest.json"

# 导入源，仅 core/ingest.py 使用，其他模块不要引用
STAGING_ROOT: Path = Path(os.getenv("KEO_STAGING_ROOT", REPO_ROOT / "temp"))

# B 的输入
BUILD_CASES_PATH: Path = DATA_DIR / "build" / "cases_build.jsonl"
BUILD_IDS_PATH: Path = DATA_DIR / "build" / "build_ids.txt"
DEV_IDS_PATH: Path = DATA_DIR / "build" / "dev_ids.txt"
SPLITS_PATH: Path = DATA_DIR / "splits" / "划分名单.json"

# D 的题库
D_QUESTION_DEMO: Path = DATA_DIR / "questions" / "questions_demo.jsonl"
D_QUESTION_FULL: Path = DATA_DIR / "questions" / "questions_full.jsonl"

# 完整包（A 的案例主表与全量三元组），仅当 ingest --include-full 时导入。
# B 不使用，留给需要原文主表或三元组的模块。
FULL_DIR: Path = DATA_DIR / "full"
CASES_PATH: Path = FULL_DIR / "案例数据.json"
TRIPLES_PATH: Path = FULL_DIR / "三元组数据.jsonl"
ALIAS_PATH: Path = FULL_DIR / "实体别名映射.json"

INDEX_ROOT: Path = OUTPUT_ROOT / "index"            # 派生：向量库
RESULT_ROOT: Path = OUTPUT_ROOT / "results"         # 派生：检索与回答

SPLITS_FALLBACK: Path = KEO_ROOT / "configs" / "splits.json"


def resolve_splits_path(explicit: Path | None = None) -> Path:
    """划分名单优先用 A 交付包里的原件；包不在时退回本地副本。"""
    if explicit:
        return Path(explicit)
    if SPLITS_PATH.exists():
        return SPLITS_PATH
    return SPLITS_FALLBACK


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
    print(f"embedding   : {EMBEDDING_MODEL}")
    print(f"回答模型    : {ANSWER_MODEL_ID}  最高 {ANSWER_MAX_TOKENS} tokens")
    print(f"类别        : {len(CATEGORIES)} 类")
    print(f"API Key     : {'已配置' if API_KEY else '缺失'}")
