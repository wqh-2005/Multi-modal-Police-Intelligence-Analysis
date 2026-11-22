"""统一运行 direct / text_rag / graph_rag，输出交付格式1.3、1.4。

从项目根目录运行：
    python -m experiments.KEO.core.run_experiment --help
    python -m experiments.KEO.core.run_experiment --questions experiments/KEO/output/questionbank/questions.jsonl --split experiments/KEO/output/test/划分名单.json --run-id smoke_001 --limit 3

正式对比前，应先完成B的文本建库、C的图谱导入与向量同步，确保两边
使用同一份build_ids。--split不会重建索引，也不会改变数据库里的建库范围。
--text-index-dir / --text-collection可选择已有的调试文本索引。
仅跑直接回答可指定--methods direct，此时无需索引和划分名单。

输出目录：output/results/<run_id>/（可用--output-dir修改根目录）
    answers.jsonl      格式1.4，交给D进行后续评分
    retrieval.jsonl    格式1.3，仅包含text_rag和graph_rag
    checkpoint.jsonl   每次完成一项就落盘；可能包含失败重试前的旧记录
    manifest.json      本次题库、参数和配置，不含API密钥或数据库密码

--resume跳过status=ok的记录，包括parse_status=error的已生成回答，避免
把分类解析失败悄悄重试成成功。后者保留给D统计；status=error的记录会重试。
修改题库或模型等参数后需新建run_id。运行期索引版本的冻结由操作者负责。

本入口只检索、生成答案，不建库、不同步向量、不运行D的评分。
每条答案立即追加到checkpoint.jsonl；中断后用原参数加--resume继续。
同一个run_id不能同时由多个进程运行；索引在实验期间应保持不变。
--workers默认1，可设3并发生成回答；BC检索各自串行，完成记录单线程落盘。
Ctrl+C后停止领取新任务，等待在途请求结束并保存；execution.json记录本次并发设置。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from experiments.KEO.configs import config
from experiments.KEO.core.text_rag.answerer import answer_one
from experiments.KEO.models.llm_client import AnswerClient

logger = logging.getLogger(__name__)
METHODS = ("direct", "text_rag", "graph_rag")
RETRIEVAL_FIELDS = ("status", "context", "evidence", "elapsed_ms", "error")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_questions(path: Path, limit: int) -> list[dict]:
    """先校验整份题库，且只向各模块传递格式1.2的三个字段。"""
    questions, seen = [], set()
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                record = {key: item[key] for key in
                          ("question_id", "question_type", "question")}
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"题库第{line_number}行不符合格式1.2") from exc
            for key, value in record.items():
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"题库第{line_number}行的{key}必须为非空字符串")
            if record["question_id"] != record["question_id"].strip():
                raise ValueError(f"题库第{line_number}行question_id含首尾空白")
            if record["question_type"] not in ("case_judgment", "global_analysis"):
                raise ValueError(f"题库第{line_number}行question_type不受支持")
            if record["question_id"] in seen:
                raise ValueError(f"重复question_id：{record['question_id']}")
            seen.add(record["question_id"])
            if not limit or len(questions) < limit:
                questions.append(record)
    if not questions:
        raise ValueError("题库为空")
    return questions


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load_checkpoint(path: Path, run_id: str, model_id: str,
                     expected: set[tuple[str, str]]) -> dict:
    """同一题/方法取最后一次结果；仅修复崩溃产生的末尾不完整行。"""
    records = {}
    if not path.exists():
        return records
    with path.open("r+b") as handle:
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                # 只有换行并fsync后才算提交，不复用末尾未提交记录。
                handle.truncate(offset)
                logger.warning("已移除checkpoint末尾未提交的半行记录")
                break
            try:
                item = json.loads(line)
                key = (item["question_id"], item["method"])
                valid = (item["run_id"] == run_id and item["model_id"] == model_id
                         and key in expected and item["status"] in ("ok", "error")
                         and all(field in item["retrieval"] for field in RETRIEVAL_FIELDS))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError("checkpoint内容损坏，不能安全续跑") from exc
            if not valid:
                raise ValueError("checkpoint与本次实验不一致")
            records[key] = item
    return records


def _export(run_dir: Path, records: dict, questions: list[dict], methods: list[str]) -> None:
    """导出时去重并恢复题库顺序；D读取answers.jsonl，无需读取checkpoint。"""
    with (run_dir / "answers.jsonl.tmp").open("w", encoding="utf-8") as answers, \
            (run_dir / "retrieval.jsonl.tmp").open("w", encoding="utf-8") as retrievals:
        for question in questions:
            for method in methods:
                item = records.get((question["question_id"], method))
                if item is None:
                    continue
                answers.write(json.dumps(item, ensure_ascii=False) + "\n")
                if method != "direct":
                    retrievals.write(json.dumps({
                        "question_id": item["question_id"], "method": method,
                        **item["retrieval"],
                    }, ensure_ascii=False) + "\n")
    for name in ("answers.jsonl", "retrieval.jsonl"):
        (run_dir / (name + ".tmp")).replace(run_dir / name)


def _failed_answer(run_id: str, question: dict, method: str,
                   model_id: str, retrieval: dict) -> dict:
    return {
        "run_id": run_id, "question_id": question["question_id"],
        "method": method, "model_id": model_id, "status": "error",
        "answer_text": "", "predicted_category": None, "cited_evidence_ids": [],
        "parse_status": "not_applicable", "retrieval": retrieval,
        "generation_elapsed_ms": 0, "error": retrieval["error"],
    }


async def _run_workers(pending, process, commit, workers):
    """Bounded workers; checkpoint commits remain on the event-loop thread.

    Cancellation stops scheduling new items and drains current synchronous HTTP
    requests before clients close. Never schedule one task for every question.
    """
    iterator = iter(pending)
    stop = asyncio.Event()

    async def worker():
        while not stop.is_set():
            try:
                item = next(iterator)
            except StopIteration:
                return
            try:
                answer = await process(*item)
                commit(answer)
            except BaseException:
                stop.set()
                raise

    tasks = [asyncio.create_task(worker()) for _ in range(min(workers, len(pending)))]
    group = asyncio.gather(*tasks, return_exceptions=True)
    try:
        outcomes = await asyncio.shield(group)
    except asyncio.CancelledError:
        stop.set()
        logger.warning("Stopping: waiting for in-flight requests to checkpoint; do not close the window")
        await asyncio.shield(group)
        raise
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome


async def run(args: argparse.Namespace) -> int:
    if not 1 <= args.workers <= 8:
        raise ValueError("--workers must be between 1 and 8")
    methods = [item.strip() for item in args.methods.split(",")]
    if not methods or len(set(methods)) != len(methods) or any(m not in METHODS for m in methods):
        raise ValueError("--methods仅允许direct,text_rag,graph_rag，不可重复")
    if args.limit < 0 or args.top_k <= 0:
        raise ValueError("--limit必须>=0，--top-k必须>0")
    if args.resume and not args.run_id:
        raise ValueError("--resume必须指定原来的--run-id")
    run_id = args.run_id or datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S_%fZ")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", run_id):
        raise ValueError("run_id限100位以内的字母、数字、下划线、点、短横线，以字母或数字开头")
    questions_path = args.questions.expanduser().resolve()
    questions = _read_questions(questions_path, args.limit)
    run_dir = args.output_dir.expanduser().resolve() / run_id

    split_path, build_ids, graph_settings = None, set(), None
    settings = {
        "run_id": run_id, "questions_path": str(questions_path),
        "questions_sha256": _sha256(questions_path), "limit": args.limit,
        "methods": methods, "model_id": config.ANSWER_MODEL_ID,
        "temperature": config.ANSWER_TEMPERATURE, "max_tokens": config.ANSWER_MAX_TOKENS,
        "answer_base_url": config.BASE_URL,
        "prompt_sha256": _sha256(Path(__file__).parent / "text_rag" / "prompt.py"),
    }
    if any(method != "direct" for method in methods):
        split_path = config.resolve_splits_path(args.split).expanduser().resolve()
        split = config.load_splits(split_path)
        for field in ("build_ids", "eval_ids"):
            values = split[field]
            if (not isinstance(values, list) or
                    any(not isinstance(value, str) or not value.strip() for value in values) or
                    len(set(values)) != len(values)):
                raise ValueError(f"划分名单{field}必须为无重复的非空字符串列表")
        build_ids = set(split["build_ids"])
        if not build_ids:
            raise ValueError("build_ids为空，拒绝不受限制的检索")
        settings.update(split_path=str(split_path), split_sha256=_sha256(split_path))
    if "text_rag" in methods:
        settings["text_rag"] = {
            "index_dir": str((args.text_index_dir or config.text_index_dir()).resolve()),
            "collection": args.text_collection or config.text_collection(),
            "top_k": args.top_k, "embedding_model": config.EMBEDDING_MODEL,
        }
    if "graph_rag" in methods:
        # 惰性加载：仅跑direct/text_rag时不要求Neo4j的环境变量。
        graph_settings = config.graph_rag_cfg
        public_graph_settings = graph_settings.model_dump(mode="json")
        settings["graph_rag"] = {
            key: value for key, value in public_graph_settings.items()
            if "PASSWORD" not in key and "API_KEY" not in key
        }
        settings["graph_rag"].update(
            SPLIT_PATH=str(split_path),
            CHROMA_PERSIST_DIR=str((args.graph_vector_dir or graph_settings.CHROMA_PERSIST_DIR).resolve()),
            CHROMA_COLLECTION=args.graph_collection or graph_settings.CHROMA_COLLECTION,
        )

    manifest_path = run_dir / "manifest.json"
    if args.resume:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous != settings:
            raise ValueError("参数、题库、划分名单或模型配置已改变，请使用新的run_id")
    elif run_dir.exists():
        raise FileExistsError(f"实验目录已存在：{run_dir}；续跑请加--resume")
    else:
        run_dir.mkdir(parents=True)
        _atomic_json(manifest_path, settings)

    # Execution concurrency is separate from answer/retrieval configuration.
    # It may change on resume without invalidating already committed answers.
    _atomic_json(run_dir / "execution.json", {
        "workers": args.workers, "started_at": datetime.now(timezone.utc).isoformat(),
        "resume": args.resume,
    })
    expected = {(q["question_id"], method) for q in questions for method in methods}
    records = _load_checkpoint(run_dir / "checkpoint.jsonl", run_id,
                               config.ANSWER_MODEL_ID, expected)
    client = embedder = graph = collection = None
    try:
        pending = [(q, m) for q in questions for m in methods
                   if records.get((q["question_id"], m), {}).get("status") != "ok"]
        if pending:
            client = AnswerClient()
        if any(m == "text_rag" for _, m in pending):
            import chromadb
            from experiments.KEO.models.embedder import Embedder
            from experiments.KEO.core.text_rag.retrieve import retrieve

            text = settings["text_rag"]
            if not Path(text["index_dir"]).is_dir():
                raise FileNotFoundError("文本索引目录不存在，请先建立文本索引")
            # 仅打开已有集合，不在检索阶段静默创建空库。
            collection = chromadb.PersistentClient(path=text["index_dir"]).get_collection(text["collection"])
            if collection.count() == 0:
                raise ValueError("文本索引为空，请先建立文本索引")
            embedder = Embedder()
        if any(m == "graph_rag" for _, m in pending):
            from experiments.KEO.core.graph_rag.api import GraphRAG

            graph_config = settings["graph_rag"]
            graph = GraphRAG(
                graph_path=str(graph_settings.DEBUG_GRAPH_PATH),
                vector_dir=graph_config["CHROMA_PERSIST_DIR"], split_path=str(split_path),
                collection_name=graph_config["CHROMA_COLLECTION"],
            )
        text_lock = asyncio.Lock()

        async def process(question, method):
            retrieval = None
            if method != "direct":
                started = perf_counter()
                try:
                    if method == "text_rag":
                        async with text_lock:
                            result = await asyncio.to_thread(
                                retrieve, question["question"], collection, embedder, build_ids, args.top_k)
                    else:
                        result = await graph.retrieve(question)
                    retrieval = {key: result[key] for key in RETRIEVAL_FIELDS}
                    if retrieval["status"] not in ("ok", "empty", "error"):
                        raise ValueError("检索返回了非法status")
                    if any(evidence["source_id"] not in build_ids for evidence in retrieval["evidence"]):
                        raise ValueError("检索证据超出build_ids范围")
                except Exception as exc:
                    retrieval = {
                        "status": "error", "context": "", "evidence": [],
                        "elapsed_ms": int((perf_counter() - started) * 1000),
                        "error": {"type": "RETRIEVAL_ERROR", "message": type(exc).__name__},
                    }
            if retrieval is not None and retrieval["status"] == "error":
                answer = _failed_answer(run_id, question, method, client.model_id, retrieval)
            else:
                answer = await asyncio.to_thread(
                    answer_one, run_id=run_id, question_id=question["question_id"],
                    question=question["question"], question_type=question["question_type"],
                    method=method, retrieval=retrieval, client=client,
                )
            return answer

        with (run_dir / "checkpoint.jsonl").open("a", encoding="utf-8", newline="\n") as journal:
            completed = 0

            def commit(answer):
                nonlocal completed
                journal.write(json.dumps(answer, ensure_ascii=False) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
                records[(answer["question_id"], answer["method"])] = answer
                completed += 1
                logger.info("[%s/%s] %s %s status=%s parse=%s workers=%s", completed, len(pending),
                            answer["question_id"], answer["method"], answer["status"],
                            answer["parse_status"], args.workers)

            await _run_workers(pending, process, commit, args.workers)

    finally:
        # 即使中途Ctrl+C或异常，也导出已经提交的结果并关闭模型/数据库连接。
        try:
            _export(run_dir, records, questions, methods)
        finally:
            try:
                if graph is not None:
                    await graph.close()
            finally:
                # 现有B客户端暂未提供public close方法，关闭其持有的OpenAI客户端。
                for resource in (embedder, client):
                    if resource is not None:
                        resource._client.close()
    errors = sum(item["status"] == "error" for item in records.values())
    parse_errors = sum(item["parse_status"] == "error" for item in records.values())
    logger.info("实验%s：%s/%s条，失败%s条，解析失败%s条；目录：%s",
                run_id, len(records), len(expected), errors, parse_errors, run_dir)
    return 1 if errors or len(records) != len(expected) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", type=Path, default=config.D_QUESTION_DEMO, help="格式1.2题库，默认Demo版；正式运行指定全量版路径")
    parser.add_argument("--methods", default=",".join(METHODS), help="逗号分隔，默认运行三种方法")
    parser.add_argument("--run-id", help="实验编号；不填则生成UTC时间编号")
    parser.add_argument("--resume", action="store_true", help="跳过已成功记录，重试失败及未完成项；参数须与原实验一致")
    parser.add_argument("--workers", type=int, default=1, help="并发任务数1到8，建议3；续跑可调整")
    parser.add_argument("--limit", type=int, default=0, help="只跑题库前N题，0表示全部")
    parser.add_argument("--output-dir", type=Path, default=config.RESULT_ROOT, help="输出根目录，下面按run_id隔离")
    parser.add_argument("--split", type=Path, help="B、C共用划分名单，默认取公共配置")
    parser.add_argument("--top-k", type=int, default=config.TOP_K, help="文本检索条数")
    parser.add_argument("--text-index-dir", type=Path, help="已有文本向量库目录")
    parser.add_argument("--text-collection", help="已有文本集合名")
    parser.add_argument("--graph-vector-dir", type=Path, help="已有图谱节点向量库目录")
    parser.add_argument("--graph-collection", help="已有图谱节点集合名")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.warning("实验已中断；使用原参数加--resume继续")
        return 130
    except Exception as exc:
        logger.error("实验未完成：%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
