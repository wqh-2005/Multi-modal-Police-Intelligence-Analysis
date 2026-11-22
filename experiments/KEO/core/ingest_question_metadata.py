"""从 A 的评估原文反推 D 的格式 1.5 金标文件。

背景：D 应该产出 `question_metadata.jsonl`（格式 1.5，含 gold_category），
但交接包与仓库里都没有该文件。A 给 D 的 `cases_eval.jsonl` 里带有评估集的
真实类别，而案例题题干就是「案情原文 + 指令」，因此可以用题干前缀匹配回原文，
还原 question → source_id → category。

这不是编造：匹配不到就写 null，并单独统计，交 D 复核。
"""
from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path

from experiments.KEO.configs import config

SPACES = re.compile(r"\s+")


def norm(text: str) -> str:
    return SPACES.sub("", text or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="从 cases_eval.jsonl 反推金标（格式 1.5）")
    parser.add_argument("--cases", default=config.EVAL_CASES_PATH)
    parser.add_argument("--questions", default=config.D_QUESTION_DEMO)
    parser.add_argument("--out", default=config.QUESTION_METADATA)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # 评估原文：按归一化文本前 KEY_LEN 字建索引。
    # 桶键必须小于最短的被索引文本，否则短案例（如 60 余字）整篇不足键长，
    # 题干前 KEY_LEN 字里会混入指令文字，桶键永远对不上。
    KEY_LEN = 30
    buckets: dict[str, list[tuple[str, str, str]]] = {}
    short_cases: list[tuple[str, str, str]] = []
    total_cases = 0
    with io.open(args.cases, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            text = norm(row.get("text", ""))
            if not text:
                continue
            total_cases += 1
            item = (text, row["source_id"], row.get("category") or "")
            if len(text) <= KEY_LEN:
                short_cases.append(item)
            else:
                buckets.setdefault(text[:KEY_LEN], []).append(item)
    print(f"评估原文 {total_cases:,} 篇，索引桶 {len(buckets):,}，短案例 {len(short_cases):,}")

    questions = [json.loads(line) for line in io.open(args.questions, encoding="utf-8") if line.strip()]
    records: list[dict] = []
    matched = globals_ = 0
    unmatched: list[str] = []

    for item in questions:
        qid = item["question_id"]
        qtype = item.get("question_type", "case_judgment")
        if qtype != "case_judgment":
            records.append({"question_id": qid, "origin_source_ids": [], "gold_category": None})
            globals_ += 1
            continue

        text = norm(item.get("question", ""))
        best: tuple[str, str, str] | None = None
        candidates = list(buckets.get(text[:KEY_LEN], [])) + short_cases
        for case_text, source_id, category in candidates:
            if text.startswith(case_text) and (best is None or len(case_text) > len(best[0])):
                best = (case_text, source_id, category)

        if best:
            records.append({"question_id": qid, "origin_source_ids": [best[1]],
                            "gold_category": best[2]})
            matched += 1
        else:
            records.append({"question_id": qid, "origin_source_ids": [], "gold_category": None})
            unmatched.append(qid)

    case_total = len(questions) - globals_
    rate = matched / case_total * 100 if case_total else 0.0
    print(f"题目 {len(questions):,} 道（案例题 {case_total:,}，全局题 {globals_}）")
    print(f"案例题匹配成功 {matched:,}  匹配率 {rate:.1f}%")
    if unmatched:
        print(f"未匹配 {len(unmatched)} 道，例如 {unmatched[:8]}")

    if args.dry_run:
        print("dry-run：未写文件")
        return 0 if not unmatched else 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"已写入 {out}（{len(records):,} 行）")
    return 0 if not unmatched else 1


if __name__ == "__main__":
    raise SystemExit(main())
