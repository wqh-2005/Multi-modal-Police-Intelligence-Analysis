"""案例数据读取与格式校验。

对应格式 1.1 §2.1：每条记录只保留 id / 案情描述 / 案件类别 三个字段。

设计原则：**早失败**。字段缺失、id 重复、类别不在统一集合内，都在读取时
立刻报错并指出具体条目，而不是等到建索引或评测时才暴露。

"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from experiments.KEO.configs import config

REQUIRED_FIELDS = ("id", "案情描述", "案件类别")


def load_cases(path: Path | None = None, ids: set[str] | None = None,
               strict: bool = True) -> list[dict]:
    """读取案例数据，可选按 id 集合过滤（用于只取 build_ids）。"""
    source = Path(path) if path else config.CASES_PATH
    if not source.exists():
        raise FileNotFoundError(f"案例数据不存在：{source}")

    with source.open(encoding="utf-8") as handle:
        cases = json.load(handle)
    if not isinstance(cases, list):
        raise ValueError(f"案例数据应为 JSON 数组，实际是 {type(cases).__name__}：{source}")

    validate_cases(cases, strict=strict)
    if ids is not None:
        cases = [case for case in cases if case["id"] in ids]
    return cases


def validate_cases(cases: list[dict], strict: bool = True) -> dict:
    """校验并返回统计信息；结构性问题直接抛异常。"""
    if not cases:
        raise ValueError("案例数据为空")

    seen: dict[str, int] = {}
    duplicates: list[str] = []
    missing_fields: list[str] = []
    empty_text: list[str] = []
    unknown_category: list[str] = []
    allowed = set(config.CATEGORIES)

    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"第 {index} 条不是对象：{type(case).__name__}")

        lost = [field for field in REQUIRED_FIELDS if field not in case]
        if lost:
            missing_fields.append(f"第{index}条缺 {lost}")
            continue

        case_id = str(case["id"]).strip()
        if not case_id:
            missing_fields.append(f"第{index}条 id 为空")
            continue

        if not str(case.get("案情描述") or "").strip():
            empty_text.append(case_id)

        if case_id in seen:
            duplicates.append(case_id)
        else:
            seen[case_id] = index

        if str(case.get("案件类别") or "") not in allowed:
            unknown_category.append(f"{case_id}: {case.get('案件类别')!r}")

    problems: list[str] = []
    if missing_fields:
        problems.append(f"字段缺失 {len(missing_fields)} 条，例如 {missing_fields[:3]}")
    if duplicates:
        problems.append(f"id 重复 {len(duplicates)} 个，例如 {duplicates[:5]}")
    if empty_text:
        problems.append(f"案情描述为空 {len(empty_text)} 条，例如 {empty_text[:5]}")
    if unknown_category:
        problems.append(f"类别不在统一集合内 {len(unknown_category)} 条，例如 {unknown_category[:5]}")

    if problems:
        message = "案例数据校验未通过：\n  - " + "\n  - ".join(problems)
        if strict:
            raise ValueError(message)
        print(f"[警告] {message}")

    return {
        "total": len(cases),
        "unique_ids": len(seen),
        "duplicates": len(duplicates),
        "missing_fields": len(missing_fields),
        "empty_text": len(empty_text),
        "unknown_category": len(unknown_category),
        "category_counts": Counter(str(case.get("案件类别")) for case in cases),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验案例数据（格式 1.1 §2.1）")
    parser.add_argument("--path", type=Path, default=None, help="案例数据路径")
    parser.add_argument("--limit", type=int, default=0, help="只显示前 N 条样例")
    args = parser.parse_args()

    cases = load_cases(args.path)
    stats = validate_cases(cases)

    print(f"文件      : {args.path or config.CASES_PATH}")
    print(f"案例数    : {stats['total']:,}   唯一 id {stats['unique_ids']:,}")
    print(f"重复/缺字段/空文本/未知类别 : "
          f"{stats['duplicates']} / {stats['missing_fields']} / "
          f"{stats['empty_text']} / {stats['unknown_category']}")
    print("类别分布  :")
    for name, count in stats["category_counts"].most_common():
        print(f"  {name:<16} {count:>7,}")

    for case in cases[: args.limit or 0]:
        print(f"\n[{case['id']}] {case['案件类别']}")
        print(f"  {case['案情描述'][:120]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
