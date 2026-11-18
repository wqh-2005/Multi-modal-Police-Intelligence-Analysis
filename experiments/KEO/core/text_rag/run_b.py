"""B 的端到端入口：题目 -> 检索 -> 回答 -> 落盘。

用法（必须在仓库根执行）：

    # 只跑直接回答：不需要索引、不需要划分名单，今天就能测
    python -m experiments.KEO.core.text_rag.run_b --questions q.jsonl --methods direct

    # 文本 RAG（需要先建索引 + 划分名单）
    python -m experiments.KEO.core.text_rag.run_b --questions q.jsonl --methods text_rag

    # 三组里的两组一起跑
    python -m experiments.KEO.core.text_rag.run_b --questions q.jsonl --methods direct,text_rag

    # 冒烟：用调试索引跑前 3 题
    python -m experiments.KEO.core.text_rag.run_b --questions q.jsonl --limit 3 --debug-index
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from experiments.KEO.configs import config
from experiments.KEO.core.text_rag.answerer import answer_one
from experiments.KEO.core.text_rag.retrieve import retrieval_record
from experiments.KEO.models.embedder import Embedder
from experiments.KEO.models.llm_client import AnswerClient

VALID_METHODS = ("direct", "text_rag", "graph_rag")


def read_jsonl(path: Path) -> list[dict]:
    """读取 JSONL（格式 1.2 的题目）。"""
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


def write_jsonl(path: Path, records: list[dict]) -> None:
    """原子写：先写 .tmp 再替换，避免中途失败留下半截文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _guard_shrink(path: Path, new_count: int, force: bool) -> None:
    """阻止用少量结果覆盖大量已有结果（例如拿 --limit 1 写进正式文件）。"""
    path = Path(path)
    if force or not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        old_count = sum(1 for line in handle if line.strip())
    if new_count < old_count:
        raise SystemExit(
            f"拒绝写入：{path} 现有 {old_count} 条，本次只产出 {new_count} 条。\n"
            "  这会丢掉已有结果。请改用 --answers-out 指定其它文件，"
            "或加 --force 明确表示要覆盖。"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="B：文本检索与直接回答基线")
    parser.add_argument("--questions", type=Path, required=True, help="格式 1.2 的 questions.jsonl")
    parser.add_argument("--methods", default="direct,text_rag",
                        help="direct,text_rag,graph_rag 的任意组合")
    parser.add_argument("--run-id", default=None, help="默认按时间生成")
    parser.add_argument("--top-k", type=int, default=config.TOP_K)
    parser.add_argument("--resume", action="store_true",
                        help="续跑：跳过结果文件里已成功的（题号, 方法），只补失败项")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 题")
    parser.add_argument("--force", action="store_true",
                        help="允许用较少结果覆盖已有结果文件（默认拒绝，防止误删）")
    parser.add_argument("--debug-index", action="store_true",
                        help="使用调试索引：不按 build_ids 过滤，结果不得用于正式实验")
    parser.add_argument("--retrieval-out", type=Path, default=None)
    parser.add_argument("--answers-out", type=Path, default=None)
    args = parser.parse_args()

    methods = [item.strip() for item in args.methods.split(",") if item.strip()]
    unknown = [item for item in methods if item not in VALID_METHODS]
    if unknown:
        parser.error(f"不支持的方法 {unknown}；B 只负责 {VALID_METHODS}")

    questions = read_jsonl(args.questions)
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        parser.error(f"题目为空：{args.questions}")

    run_id = args.run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    retrieval_out = args.retrieval_out or config.retrieval_path(args.debug_index)
    answers_out = args.answers_out or config.answers_path(args.debug_index)

    build_ids: set[str] = set()
    collection = None
    if "text_rag" in methods:
        # 检索模块按需导入：只跑 direct 时不要求它存在
        try:
            from experiments.KEO.core.text_rag.retrieve import (
                open_collection,
                retrieve,
            )
        except ImportError as exc:
            parser.error(f"text_rag 需要 core/text_rag/retrieve.py，尚未就绪：{exc}")

        if args.debug_index:
            index_dir = config.text_index_dir(True)
            collection_name = config.text_collection(True)
            print("[调试索引] 不按 build_ids 过滤，结果不得用于正式实验")
        else:
            build_ids = set(config.load_splits()["build_ids"])
            index_dir = config.text_index_dir(False)
            collection_name = config.text_collection(False)
        collection = open_collection(index_dir, collection_name)
        print(f"索引 {index_dir}  共 {collection.count():,} 条  build_ids {len(build_ids):,} 篇")

    client = AnswerClient()
    embedder = Embedder() if "text_rag" in methods else None

    # 图谱 RAG 由 C 提供，接口是 async；整轮共用一个事件循环，
    # 因为 GraphRAG 要求实例在同一循环内复用。
    graph_loop = None
    graph_rag = None
    if "graph_rag" in methods:
        import asyncio

        try:
            from experiments.KEO.core.graph_rag.api import GraphRAG
        except ImportError as exc:
            parser.error(f"graph_rag 需要 core/graph_rag/api.py：{exc}")

        graph_loop = asyncio.new_event_loop()
        graph_rag = GraphRAG(
            graph_path="unused",
            vector_dir=str(config.OUTPUT_ROOT / "test" / "node_vectors"),
            split_path=str(config.resolve_splits_path()),
        )
        print("图谱 RAG 已初始化；知识库非 ready 时检索会返回 error")
    print(f"回答模型 {client.model_id}")
    print(f"run_id {run_id}  题目 {len(questions):,} 道  方法 {methods}")

    # 断点续跑：只补 status!=ok 的（题号, 方法），已成功的绝不重复调用模型
    done_pairs: set[tuple[str, str]] = set()
    existing_answers: list[dict] = []
    existing_retrieval: list[dict] = []
    if args.resume:
        if answers_out.exists():
            existing_answers = read_jsonl(answers_out)
            done_pairs = {(r["question_id"], r["method"]) for r in existing_answers
                          if r.get("status") == "ok"}
        if retrieval_out.exists():
            existing_retrieval = read_jsonl(retrieval_out)
        print(f"续跑：已有成功回答 {len(done_pairs):,} 条，本次只补失败项")

    retrieval_records: list[dict] = []
    answer_records: list[dict] = []
    started = time.perf_counter()

    for index, item in enumerate(questions, start=1):
        missing = [key for key in ("question_id", "question") if key not in item]
        if missing:
            raise ValueError(f"第 {index} 题缺少字段 {missing}：{item}")

        question_id = item["question_id"]
        question = item.get("question", "")
        question_type = item.get("question_type", "case_judgment")

        for method in methods:
            if (question_id, method) in done_pairs:
                continue
            if method == "text_rag":
                result = retrieve(question, collection, embedder,
                                  build_ids, top_k=args.top_k)
                retrieval_records.append(retrieval_record(question_id, method, result))
                payload = result
            elif method == "graph_rag":
                result = graph_loop.run_until_complete(graph_rag.retrieve({
                    "question_id": question_id,
                    "question_type": question_type,
                    "question": question,
                }))
                retrieval_records.append(retrieval_record(question_id, method, result))
                payload = result
            else:
                payload = None
            answer_records.append(answer_one(
                run_id=run_id,
                question_id=question_id,
                question=question,
                question_type=question_type,
                method=method,
                retrieval=payload,
                client=client,
            ))

        if index % 10 == 0:
            rate = index / max(time.perf_counter() - started, 1e-6)
            print(f"  {index:,}/{len(questions):,} 题  {rate:.2f} 题/秒", flush=True)

    # 合并续跑：以（题号, 方法）为键，本次的结果覆盖旧的
    merged_answers = {(r["question_id"], r["method"]): r for r in existing_answers}
    merged_answers.update({(r["question_id"], r["method"]): r for r in answer_records})
    final_answers = [merged_answers[key] for key in sorted(merged_answers)]

    merged_retrieval = {(r["question_id"], r["method"]): r for r in existing_retrieval}
    merged_retrieval.update({(r["question_id"], r["method"]): r for r in retrieval_records})
    final_retrieval = [merged_retrieval[key] for key in sorted(merged_retrieval)]

    if final_retrieval:
        write_jsonl(retrieval_out, final_retrieval)
        print(f"检索结果 {len(final_retrieval):,} 条 -> {retrieval_out}")
    # 防覆盖护栏：本次结果比磁盘上已有的少，说明会丢掉旧记录。
    # 典型误用是拿 --limit 1 做冒烟，却指向正式结果文件。
    _guard_shrink(answers_out, len(final_answers), args.force)
    if final_retrieval:
        _guard_shrink(retrieval_out, len(final_retrieval), args.force)
    write_jsonl(answers_out, final_answers)
    print(f"回答     {len(final_answers):,} 条 -> {answers_out}")

    failed = sum(1 for record in final_answers if record["status"] != "ok")
    unparsed = sum(1 for record in final_answers if record["parse_status"] == "error")
    cited = sum(1 for record in final_answers if record["cited_evidence_ids"])
    print(f"本次调用 {len(answer_records):,} 条  全量失败 {failed}  全量解析失败 {unparsed}  "
          f"全量带引用 {cited}  耗时 {time.perf_counter() - started:.1f}s")

    if graph_rag is not None:
        try:
            graph_loop.run_until_complete(graph_rag.close())
        except Exception:  # noqa: BLE001 - 收尾失败不影响已落盘结果
            pass
        graph_loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
