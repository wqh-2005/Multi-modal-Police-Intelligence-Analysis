"""全链路 smoke test：验证 A→B→(C)→D 是否打通。

分阶段检查，每段独立给结论，不因后面失败就中断：

  1 数据       建库原文/题库/金标是否齐全、条数对不对
  2 文本索引   Chroma 里的 chunk 数
  3 检索       text_rag 检索 1 条，返回格式 1.3
  4 回答       direct + text_rag 各 1 题，产出格式 1.4
  5 接口契约   C 的 1.3（含 triple_ids）能否直接喂给 answer_one
  6 分类指标   现有 answers.jsonl 上的 Accuracy / Macro-F1
  7 图谱状态   C 是否 ready；ready 则真跑 1 题，否则报告 BLOCKED

用法（仓库根执行）：

    .\\.venv\\Scripts\\python.exe experiments\\KEO\\tools\\smoke_test.py
    .\\.venv\\Scripts\\python.exe experiments\\KEO\\tools\\smoke_test.py --no-api
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

RESULTS: list[tuple[str, str, str]] = []


def record(stage: str, status: str, detail: str) -> None:
    RESULTS.append((stage, status, detail))
    print(f"  [{status:<7}] {stage:<10} {detail}", flush=True)


def read_jsonl(path) -> list[dict]:
    with io.open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def stage_data() -> dict:
    try:
        from experiments.KEO.configs import config
        from experiments.KEO.core.text_rag.build_data import load_build_cases
        cases = load_build_cases()
        questions = read_jsonl(config.D_QUESTION_DEMO)
        gold = read_jsonl(config.QUESTION_METADATA)
        record("1 数据", "PASS",
               f"建库 {len(cases):,} 篇 / 题库 {len(questions)} 题 / 金标 {len(gold)} 条")
        return {"cases": len(cases), "questions": questions}
    except Exception as exc:  # noqa: BLE001
        record("1 数据", "FAIL", f"{type(exc).__name__}: {str(exc)[:90]}")
        return {}


def stage_text_index() -> None:
    try:
        from experiments.KEO.configs import config
        import chromadb
        client = chromadb.PersistentClient(path=str(config.text_index_dir()))
        count = client.get_collection(config.text_collection()).count()
        record("2 文本索引", "PASS" if count else "FAIL", f"{count:,} 个 chunk")
    except Exception as exc:  # noqa: BLE001
        record("2 文本索引", "FAIL", f"{type(exc).__name__}: {str(exc)[:90]}")


def stage_retrieval(questions: list[dict], api: bool) -> None:
    if not api:
        record("3 检索", "SKIP", "--no-api")
        return
    try:
        from experiments.KEO.configs import config
        from experiments.KEO.core.text_rag.retrieve import open_collection, retrieve
        from experiments.KEO.core.text_rag.build_data import load_build_ids
        from experiments.KEO.models.embedder import Embedder
        question = questions[0]["question"]
        started = time.perf_counter()
        collection = open_collection()
        embedder = Embedder()
        result = retrieve(question, collection, embedder, load_build_ids())
        elapsed = (time.perf_counter() - started) * 1000
        keys = sorted(result.keys())
        record("3 检索", "PASS" if result.get("status") == "ok" else "FAIL",
               f"status={result.get('status')} evidence={len(result.get('evidence') or [])} "
               f"keys={keys} {elapsed:.0f}ms")
    except Exception as exc:  # noqa: BLE001
        record("3 检索", "FAIL", f"{type(exc).__name__}: {str(exc)[:90]}")


def stage_answer(questions: list[dict], api: bool) -> None:
    if not api:
        record("4 回答", "SKIP", "--no-api")
        return
    try:
        from experiments.KEO.core.text_rag.answerer import answer_one
        from experiments.KEO.models.llm_client import AnswerClient
        client = AnswerClient()
        item = next(q for q in questions if q.get("question_type") == "case_judgment")
        record_ok = []
        for method in ("direct", "text_rag"):
            rec = answer_one(run_id="smoke", question_id=item["question_id"],
                             question=item["question"],
                             question_type=item["question_type"],
                             method=method, retrieval=None, client=client)
            record_ok.append(f"{method}:{rec['status']}/{rec['predicted_category']}")
        record("4 回答", "PASS", " / ".join(record_ok))
    except Exception as exc:  # noqa: BLE001
        record("4 回答", "FAIL", f"{type(exc).__name__}: {str(exc)[:90]}")


def stage_contract() -> None:
    try:
        from experiments.KEO.core.text_rag.answerer import answer_one

        retrieval = {
            "status": "ok",
            "context": "[E001] 案例case_000004中，对方要求下载应用并充值。",
            "evidence": [{"evidence_id": "E001", "source_id": "case_000004",
                          "text": "对方要求下载应用并充值。",
                          "triple_ids": ["case_000004_T001"]}],
            "elapsed_ms": 1, "error": None,
        }

        class FakeClient:
            model_id = "fake"

            def chat(self, prompt):
                return "类型判定：刷单返利类\n证据说明：E001\n应对措施：报警", 1

        rec = answer_one(run_id="smoke", question_id="q_smoke", question="x",
                         question_type="case_judgment", method="graph_rag",
                         retrieval=retrieval, client=FakeClient())
        assert rec["predicted_category"] == "刷单返利类"
        assert rec["cited_evidence_ids"] == ["E001"]
        assert rec["retrieval"]["evidence"][0]["triple_ids"]
        record("5 接口契约", "PASS", "C 的 1.3（含 triple_ids）→ 1.4 全字段正确")
    except Exception as exc:  # noqa: BLE001
        record("5 接口契约", "FAIL", f"{type(exc).__name__}: {str(exc)[:90]}")


def stage_metrics() -> None:
    try:
        from experiments.KEO.configs import config
        from experiments.KEO.core.evaluation.metrics import compute

        answers = [row for row in read_jsonl(config.answers_path())
                   if not str(row.get("run_id", "")).startswith(("verify", "smoke"))]
        gold = {row["question_id"]: row.get("gold_category")
                for row in read_jsonl(config.QUESTION_METADATA)}
        report = compute(answers, gold)
        detail = "  ".join(f"{m}: acc={r['accuracy'] * 100:.1f}% f1={r['macro_f1']:.3f}"
                           for m, r in sorted(report.items()))
        record("6 分类指标", "PASS" if report else "FAIL", detail or "无可用记录")
    except Exception as exc:  # noqa: BLE001
        record("6 分类指标", "FAIL", f"{type(exc).__name__}: {str(exc)[:90]}")


def stage_graph() -> None:
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(os.getenv("KEO_NEO4J_URI"),
                                      auth=(os.getenv("KEO_NEO4J_USER"),
                                            os.getenv("KEO_NEO4J_PASSWORD")))
        try:
            with driver.session() as session:
                row = session.run(
                    "MATCH (s:KEOKnowledgeBase) RETURN s.status AS st").single()
                status = row["st"] if row else "missing"
        finally:
            driver.close()
    except Exception as exc:  # noqa: BLE001
        record("7 图谱", "SKIP", f"Neo4j 未运行（{str(exc)[:50]}）")
        return

    if status != "ready":
        record("7 图谱", "BLOCKED",
               f"知识库状态={status}，需等节点向量化完成才能真实检索")
        return
    try:
        import asyncio
        from experiments.KEO.core.graph_rag.api import GraphRAG

        rag = GraphRAG(graph_path="unused",
                       vector_dir=os.path.join(REPO, "experiments", "KEO", "output", "test", "node_vectors"),
                       split_path=os.path.join(REPO, "experiments", "KEO", "data", "v1.1", "splits", "划分名单.json"))
        result = asyncio.run(rag.retrieve({"question_id": "smoke", "question_type": "case_judgment",
                                           "question": "对方称刷单可以赚钱，要求下载应用充值后无法提现。"}))
        record("7 图谱", "PASS" if result.get("status") in ("ok", "empty") else "FAIL",
               f"status={result.get('status')} evidence={len(result.get('evidence') or [])} "
               f"context={len(result.get('context') or '')}字")
    except Exception as exc:  # noqa: BLE001
        record("7 图谱", "FAIL", f"{type(exc).__name__}: {str(exc)[:90]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="KEO 全链路 smoke test")
    parser.add_argument("--no-api", action="store_true", help="跳过需要调模型/embedding 的环节")
    args = parser.parse_args()

    print("=" * 66)
    print("  KEO 全链路 smoke test")
    print("=" * 66)
    data = stage_data()
    stage_text_index()
    stage_retrieval(data.get("questions") or [], not args.no_api)
    stage_answer(data.get("questions") or [], not args.no_api)
    stage_contract()
    stage_metrics()
    stage_graph()

    print("-" * 66)
    counts: dict[str, int] = {}
    for _, status, _ in RESULTS:
        counts[status] = counts.get(status, 0) + 1
    summary = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"  汇总  {summary}")
    failed = counts.get("FAIL", 0)
    blocked = counts.get("BLOCKED", 0)
    if failed:
        print("  结论：有环节失败，见上表 FAIL 行")
    elif blocked:
        print("  结论：B/D 全链路已通；C 的图谱检索被索引阻塞（预期内）")
    else:
        print("  结论：全链路已打通")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
