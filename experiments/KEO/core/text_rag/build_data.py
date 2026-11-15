"""A 给 B 的建库数据包读取。

A v1.1 冻结包（默认路径 ``config.A_B_PACKAGE``）包含：

    cases_build.jsonl   49,289 篇建库案例原文
    build_ids.txt       权威建库名单，与 C/D 同源
    dev_ids.txt         22 条开发集，**只用于调参**，不计入评估

包内字段是 ``source_id / category / text / split``，与格式 1.1 的
``id / 案情描述 / 案件类别`` 不同。本模块负责归一化，下游（clause、
build_index）不必区分数据来源。

终端中文乱码先执行 chcp 65001。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.KEO.configs import config
from experiments.KEO.core.text_rag.cases import validate_cases


def _normalize(record: dict) -> dict:
    """把 A 的字段名折算成内部统一字段。"""
    return {
        "id": str(record.get("source_id", "")).strip(),
        "案情描述": record.get("text", "") or "",
        "案件类别": record.get("category", "") or "",
        "split": record.get("split", ""),
    }


def read_jsonl(path: Path) -> list[dict]:
    items: list[dict] = []
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} 第 {number} 行不是合法 JSON：{exc}") from exc
    return items


def load_build_cases(path: Path | None = None, limit: int = 0,
                     strict: bool = True) -> list[dict]:
    """读取建库案例原文，归一化成 ``{id, 案情描述, 案件类别, split}``。"""
    source = Path(path) if path else config.BUILD_CASES_PATH
    if not source.exists():
        raise FileNotFoundError(
            f"建库数据包不存在：{source}\n"
            "该文件由 A 交付（v1.1，格式 1.1）。"
        )

    cases = [_normalize(record) for record in read_jsonl(source)]
    if limit:
        cases = cases[:limit]
    validate_cases(cases, strict=strict)
    return cases


def load_ids(path: Path | None = None) -> list[str]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"名单文件不存在：{target}")
    with target.open(encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def load_build_ids() -> list[str]:
    """权威建库名单，应与划分名单的 build_ids 完全一致。"""
    return load_ids(config.BUILD_IDS_PATH)


def load_dev_ids() -> list[str]:
    """22 条开发集，只用于调参（切块大小、top_k），不得用评估集调参。"""
    return load_ids(config.DEV_IDS_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 A 给 B 的建库数据包")
    parser.add_argument("--limit", type=int, default=0, help="只校验前 N 篇")
    args = parser.parse_args()

    cases = load_build_cases(limit=args.limit)
    stats = validate_cases(cases)
    build_ids = set(load_build_ids())
    dev_ids = set(load_dev_ids())
    case_ids = {case["id"] for case in cases}

    print(f"建库案例      : {stats['total']:,}   唯一 id {stats['unique_ids']:,}")
    print(f"异常计数      : 重复 {stats['duplicates']} / 缺字段 {stats['missing_fields']} / "
          f"空文本 {stats['empty_text']} / 未知类别 {stats['unknown_category']}")
    print(f"build_ids.txt : {len(build_ids):,}")
    print(f"dev_ids.txt   : {len(dev_ids):,}（应全部属于建库集：{dev_ids <= build_ids}）")
    if not args.limit:
        print(f"与 build_ids 是否一致：{case_ids == build_ids}")
    print("类别分布      :")
    for name, count in stats["category_counts"].most_common():
        print(f"  {name:<16} {count:>7,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
