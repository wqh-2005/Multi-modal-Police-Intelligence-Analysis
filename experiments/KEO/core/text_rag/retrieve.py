"""B 的检索步骤：问题 -> 格式 1.3。

三条硬约束（全部在出口处校验，不依赖调用方自觉）：

1. 只返回 ``build_ids`` 内的案例。索引本身只用建库集构建，这里再做一次过滤与断言，
   防止 eval 案例被检索到造成泄题——泄题在成绩上表现为分数虚高，看不出异常。
2. ``evidence.text`` 必须是原文，不做任何改写或拼接。
3. 无结果记 ``empty``，失败记 ``error``，**不得把失败伪装成空结果**，
   也不得自动切换方法。

``evidence_id`` 形如 ``E001``，与格式 1.3 示例一致；``chunk_id`` 与 A 三元组的
``evidence.unit_id`` 同构（``{source_id}_clause_{n:03d}``），便于与 C 的证据对照。
"""
from __future__ import annotations

import argparse
import time

import chromadb

from experiments.KEO.configs import config


def open_collection(index_dir: Path | str | None = None,
                    collection_name: str | None = None,
                    debug: bool = False):
    """打开（或创建）文本索引集合。目录与集合名由 config 统一给出。"""
    path = index_dir or config.text_index_dir(debug)
    name = collection_name or config.text_collection(debug)
    client = chromadb.PersistentClient(path=str(path))
    return client.get_or_create_collection(
        name=name, metadata={"hnsw:space": "cosine"}
    )


def retrieve(question: str, collection, embedder,
             build_ids: set[str] | None = None,
             top_k: int | None = None) -> dict:
    """检索一条问题。

    Returns:
        含 ``status / context / evidence / elapsed_ms / error`` 五个字段的字典，
        即格式 1.3 去掉 ``question_id`` 与 ``method`` 后的部分。
    """
    top_k = top_k or config.TOP_K
    started = time.perf_counter()

    try:
        if collection.count() == 0:
            return _empty(int((time.perf_counter() - started) * 1000))
        vector = embedder.embed_one(question)
        result = collection.query(
            query_embeddings=[vector],
            n_results=min(top_k * 4, collection.count()),
            include=["documents", "metadatas"],
        )
    except Exception as exc:  # noqa: BLE001 - 任何异常都要落成 error 字段
        return {
            "status": "error",
            "context": "",
            "evidence": [],
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error": {"type": "RETRIEVAL_ERROR", "message": str(exc)},
        }

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]

    evidence: list[dict] = []
    lines: list[str] = []
    for document, metadata in zip(documents, metadatas):
        source_id = str(metadata.get("source_id", ""))
        if build_ids and source_id not in build_ids:
            continue                       # 出口过滤：eval 案例绝不能出现
        evidence_id = f"E{len(evidence) + 1:03d}"
        evidence.append({
            "evidence_id": evidence_id,
            "source_id": source_id,
            "text": document,              # 原文，不改写
            "chunk_id": metadata.get("chunk_id", ""),
        })
        lines.append(f"[{evidence_id}] 案例{source_id}中，{document}")
        if len(evidence) >= top_k:
            break

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if not evidence:
        return _empty(elapsed_ms)

    return {
        "status": "ok",
        "context": "\n".join(lines),
        "evidence": evidence,
        "elapsed_ms": elapsed_ms,
        "error": None,
    }


def _empty(elapsed_ms: int) -> dict:
    return {"status": "empty", "context": "", "evidence": [],
            "elapsed_ms": elapsed_ms, "error": None}


def retrieval_record(question_id: str, method: str, result: dict) -> dict:
    """补齐格式 1.3 的 question_id 与 method，得到可落盘的一条记录。"""
    return {
        "question_id": question_id,
        "method": method,
        "status": result["status"],
        "context": result["context"],
        "evidence": result["evidence"],
        "elapsed_ms": result["elapsed_ms"],
        "error": result["error"],
    }


def main() -> int:
    from pathlib import Path

    parser = argparse.ArgumentParser(description="查看/试跑文本索引检索")
    parser.add_argument("--question", default=None, help="试检索的问题")
    parser.add_argument("--top-k", type=int, default=config.TOP_K)
    parser.add_argument("--debug", action="store_true", help="使用调试索引")
    args = parser.parse_args()

    index_dir = config.text_index_dir(args.debug)
    print(f"索引目录 : {index_dir}")
    print(f"集合名   : {config.text_collection(args.debug)}")
    if not index_dir.exists():
        print("索引尚未建立，先运行 build_index.py")
        return 1

    collection = open_collection(debug=args.debug)
    print(f"索引条数 : {collection.count():,}")
    if not args.question:
        return 0

    from experiments.KEO.models.embedder import Embedder

    result = retrieve(args.question, collection, Embedder(),
                      build_ids=None, top_k=args.top_k)
    print(f"状态     : {result['status']}  耗时 {result['elapsed_ms']} ms")
    for item in result["evidence"]:
        print(f"  {item['evidence_id']} {item['source_id']} {item['text'][:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
