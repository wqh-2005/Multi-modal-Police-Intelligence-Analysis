"""分句与 chunk 构造。

chunk_id 与 A 的三元组 ``evidence.unit_id`` 采用同一命名：

    {source_id}_clause_{n:03d}

分句规则与 ``app/relationextraction/code/llmuse.py::split_case`` 完全一致
（在 。！？!?；; 之后切开），因此 B 的文本证据与 C 的图谱证据可以逐条对照，
D 也能用同一套编号核对「证据指向」。

终端中文乱码时先执行：chcp 65001
"""
from __future__ import annotations

import argparse
import re
from typing import Iterable

# 与 llmuse.py 保持一致：在句末标点之后切开，保留标点
_SPLITTER = re.compile(r"(?<=[。！？!?；;])")


def split_clauses(source_id: str, text: str) -> list[dict]:
    """把一篇案情切成句子单元。

    Args:
        source_id: 案例编号，用于生成 chunk_id。
        text: 案情原文。

    Returns:
        [{chunk_id, source_id, text}]，按原文顺序编号，从 1 开始。
    """
    parts = [part.strip() for part in _SPLITTER.split(text or "") if part.strip()]
    if not parts:
        parts = [(text or "").strip()]
    return [
        {
            "chunk_id": f"{source_id}_clause_{index:03d}",
            "source_id": source_id,
            "text": part,
        }
        for index, part in enumerate(parts, start=1)
    ]


def build_chunks(cases: Iterable[dict]) -> list[dict]:
    """cases 为 [{id, 案情描述, 案件类别}]，展开成全部 chunk。"""
    chunks: list[dict] = []
    for case in cases:
        chunks.extend(split_clauses(case["id"], case.get("案情描述", "")))
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser(description="查看分句结果")
    parser.add_argument("--limit", type=int, default=2, help="显示前 N 篇")
    parser.add_argument("--max-chunks", type=int, default=8, help="每篇最多显示几个 chunk")
    args = parser.parse_args()

    from experiments.KEO.core.text_rag.cases import load_cases

    cases = load_cases()[: args.limit]
    total = 0
    for case in cases:
        chunks = split_clauses(case["id"], case["案情描述"])
        total += len(chunks)
        print(f"\n[{case['id']}] 共 {len(chunks)} 句")
        for chunk in chunks[: args.max_chunks]:
            print(f"  {chunk['chunk_id']}  {chunk['text'][:80]}")
        if len(chunks) > args.max_chunks:
            print(f"  … 还有 {len(chunks) - args.max_chunks} 句")

    print(f"\n合计 {total} 个 chunk（{len(cases)} 篇）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
