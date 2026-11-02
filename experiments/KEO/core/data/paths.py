"""KEO 数据模块的路径约定。

约定：
- 代码：`experiments/KEO/core/data/`
- 本地数据：`experiments/KEO/data/person_a/`（不入 git，可用 `KEO_DATA_ROOT` 覆盖）
- 上游抽取产物：`app/relationextraction/output/case/`（保留原位置，只读）
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "app").is_dir() and (candidate / "experiments").is_dir():
            return candidate
    raise RuntimeError(f"无法从 {start} 向上定位仓库根目录（需同时含 app/ 与 experiments/）")


REPO_ROOT = _find_repo_root(Path(__file__).resolve())
KEO_DIR = REPO_ROOT / "experiments" / "KEO"
DATA_ROOT = Path(os.environ.get("KEO_DATA_ROOT") or (KEO_DIR / "data")).expanduser().resolve()

PERSON_A_DIR = DATA_ROOT / "person_a"
NAME_CLEANING_DIR = PERSON_A_DIR / "name_cleaning"
# 清洗产物按版本分目录，冻结版本不覆盖
CLEANING_DIR_V10 = NAME_CLEANING_DIR / "apply_v1.0"
CLEANING_DIR = NAME_CLEANING_DIR / "apply_v1.1"
# 交付包：handoff/ 为 1.0-final（保留不动），handoff_v1.1/ 为当前版本
HANDOFF_ROOT = PERSON_A_DIR / "handoff"
HANDOFF_DIR = HANDOFF_ROOT / "keo"
HANDOFF_ROOT_V11 = PERSON_A_DIR / "handoff_v1.1"
HANDOFF_DIR_V11 = HANDOFF_ROOT_V11 / "keo"
REVIEW_RESOLUTION_DIR = NAME_CLEANING_DIR / "review_resolution"

# 上游抽取产物（只读，不迁移）
CASE_DIR = REPO_ROOT / "app" / "relationextraction" / "output" / "case"
EXTRACTION_DIR = REPO_ROOT / "app" / "relationextraction"
RAW_CASES = REPO_ROOT / "data" / "raw" / "诈骗案例数据集_带编号.json"
MERGE_MANIFEST = CASE_DIR / "collaboration_merge_manifest.json"

CASE_ID_RE_TEXT = r"^case_\d{6}$"


def rel(path: Path | str) -> str:
    """仓库根目录相对路径（posix 斜杠），便于跨机器复现。"""
    path = Path(path)
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def abs_path(value: Any) -> Path:
    """manifest 中可能保存相对仓库根目录的路径，也可能是绝对路径。"""
    if isinstance(value, dict):
        value = value["path"]
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path)
