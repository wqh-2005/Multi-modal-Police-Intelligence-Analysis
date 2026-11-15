"""B 的建库步骤：建库案例原文 -> 分句 -> 向量索引。

用法（仓库根执行）：

    python -m experiments.KEO.core.text_rag.build_index --limit 300 --debug    # 冒烟
    python -m experiments.KEO.core.text_rag.build_index                        # 正式（49,289 篇）

数据源固定为 A 给 B 的专用包（``config.BUILD_CASES_PATH``，已按 build_ids 过滤），
因此 eval 案例不可能进入索引。这是防泄题的第一道闸门，第二道在 retrieve.py 的出口过滤。

chunk 粒度：一个 chunk = 一个分句，``chunk_id = {source_id}_clause_{n:03d}``，
与 A 三元组的 ``evidence.unit_id`` 同构，B 和 C 的证据可逐条对照。
分句过短会影响检索质量，可用 ``--window`` 合并相邻分句（默认 1，不合并）；
窗口内首句决定 chunk_id，证据仍按原文返回。

断点续跑：已完成的案例编号记录在索引目录的 ``done_cases.json``，
中断后重跑会跳过。调试索引与正式索引目录、集合名都不同，不会混用。

默认参数是实测选出来的：300 篇案例上比较 window=1/min_chars=0 与
window=3/min_chars=8，后者命中「受害人被以刷单的方式诈骗，共计被骗 8887 元」
这类实质内容，前者只命中「手段：刷单类诈骗。」这类标签句；同时 chunk 数
由 1,493 降到 569，建索引更快。参数仍可在 dev_ids 上继续调。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import chromadb

from experiments.KEO.configs import config
from experiments.KEO.core.text_rag.build_data import load_build_cases
from experiments.KEO.core.text_rag.clause import split_clauses
from experiments.KEO.models.embedder import Embedder


def _chunks_of(case: dict, window: int, min_chars: int = 0,
               max_chars: int = 800) -> list[dict]:
    """把一篇案例切成 chunk。

    Args:
        window: 相邻分句合并数。1 表示不合并。
        min_chars: 过滤掉长度不足的碎片。实测 8.9% 的分句是
            「卡号：」「无」「？」这类被切断的字段标签，语义为空却极易
            被检索命中，会把真正的案情内容挤出 top_k。
    """
    parts = [item for item in split_clauses(case["id"], case["案情描述"])
             if len(item["text"]) >= min_chars]
    if not parts:
        return []
    if window <= 1:
        return parts

    merged: list[dict] = []
    group: list[dict] = []
    group_chars = 0

    def close_group() -> None:
        nonlocal group, group_chars
        merged.append({
            "chunk_id": group[0]["chunk_id"],      # 首句编号，保持与 unit_id 对齐
            "source_id": case["id"],
            "text": "".join(item["text"] for item in group),
        })
        group = []
        group_chars = 0

    for part in parts:
        # 合并到 window 句，或再加一句会超过单块上限时收口，
        # 避免出现几千字的长块把 embedding 请求撑爆
        if group and (len(group) >= window
                      or group_chars + len(part["text"]) > max_chars):
            close_group()
        group.append(part)
        group_chars += len(part["text"])
    if group:
        close_group()
    return merged


def _read_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as handle:
        return set(json.load(handle))


def _write_done(path: Path, done: set[str]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(done), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="建立 KEO 文本检索索引")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 篇（冒烟）")
    parser.add_argument("--window", type=int, default=3,
                        help="相邻分句合并数，默认 3（实测见模块说明）")
    parser.add_argument("--min-chars", type=int, default=8,
                        help="丢弃长度不足的分句碎片，默认 8；设 0 关闭过滤")
    parser.add_argument("--batch", type=int, default=config.EMBED_BATCH, help="每次 embedding 条数")
    parser.add_argument("--max-chars", type=int, default=800,
                        help="合并后单块的字符目标上限；单句本身超长时不切分，默认 800")
    parser.add_argument("--max-batch-chars", type=int, default=6000,
                        help="单次请求的字符预算，默认 6000。超了接口会返回 20015")
    parser.add_argument("--debug", action="store_true",
                        help="写调试索引（独立目录与集合名），不用于正式实验")
    args = parser.parse_args()

    index_dir = config.text_index_dir(args.debug)
    collection_name = config.text_collection(args.debug)
    index_dir.mkdir(parents=True, exist_ok=True)

    cases = load_build_cases(limit=args.limit)
    print(f"数据源   : {config.BUILD_CASES_PATH}")
    print(f"案例     : {len(cases):,} 篇")
    print(f"索引     : {index_dir}  集合 {collection_name}  window={args.window}")

    client = chromadb.PersistentClient(path=str(index_dir))
    collection = client.get_or_create_collection(
        name=collection_name, metadata={"hnsw:space": "cosine"}
    )
    done_path = index_dir / "done_cases.json"
    done = _read_done(done_path)
    embedder = Embedder()
    print(f"embedding: {embedder.model}   已完成 {len(done):,} 篇   索引内 {collection.count():,} 条")

    started = time.perf_counter()
    added_chunks = 0
    added_cases = 0
    buffer: list[dict] = []          # 待发送的 chunk，跨案例累积
    buffer_cases: list[str] = []     # 这批 chunk 覆盖的案例编号

    def flush() -> None:
        """把缓冲区写入索引：同时受条数与字符预算约束。"""
        nonlocal buffer, buffer_cases, added_chunks
        offset = 0
        while offset < len(buffer):
            batch: list[dict] = []
            chars = 0
            while offset < len(buffer) and len(batch) < args.batch:
                item = buffer[offset]
                if batch and chars + len(item["text"]) > args.max_batch_chars:
                    break
                batch.append(item)
                chars += len(item["text"])
                offset += 1
            vectors = embedder.embed([item["text"] for item in batch])
            # 用 upsert 而非 add：中途失败重跑时同一 chunk_id 会被覆盖而不是报冲突
            collection.upsert(
                ids=[item["chunk_id"] for item in batch],
                documents=[item["text"] for item in batch],
                metadatas=[{"source_id": item["source_id"], "chunk_id": item["chunk_id"]}
                           for item in batch],
                embeddings=vectors,
            )
            added_chunks += len(batch)
        # 只有 chunk 真正写入后，才把这些案例标记为已完成
        done.update(buffer_cases)
        buffer = []
        buffer_cases = []

    for number, case in enumerate(cases, start=1):
        if case["id"] in done:
            continue
        buffer.extend(_chunks_of(case, args.window, args.min_chars, args.max_chars))
        buffer_cases.append(case["id"])
        added_cases += 1

        # 攒够一个 batch 才发请求：单篇只有约 5 个分句，逐篇发送会把
        # 49,289 篇变成 49,289 次 HTTP 调用，实测要 13 小时以上
        if (len(buffer) >= args.batch
                or sum(len(item["text"]) for item in buffer) >= args.max_batch_chars):
            flush()
            if added_cases % 200 == 0:
                _write_done(done_path, done)
                elapsed = time.perf_counter() - started
                rate = added_cases / max(elapsed, 1e-6)
                print(f"  {number:,}/{len(cases):,} 篇  新增 {added_chunks:,} chunk  "
                      f"{rate:.1f} 篇/秒  预计剩余 "
                      f"{(len(cases) - number) / max(rate, 1e-6) / 60:.1f} 分钟",
                      flush=True)

    if buffer:
        flush()

    _write_done(done_path, done)
    elapsed = time.perf_counter() - started
    print(f"\n完成：本次新增 {added_chunks:,} chunk / {added_cases:,} 篇，"
          f"耗时 {elapsed / 60:.1f} 分钟，索引内共 {collection.count():,} 条")
    if added_cases:
        print(f"速率 {added_cases / elapsed:.1f} 篇/秒，"
              f"按此推算全量 49,289 篇约 {49289 / (added_cases / elapsed) / 3600:.1f} 小时")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
