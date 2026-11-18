"""D 的逐条评分：读取格式 1.4 的回答，用评判模型按五维打分，产出格式 1.6。

与原 `run_eval.py` 的差别只在**接线方式**，评分细则与输出结构保持原样：

- 路径、模型、密钥改为读 `config` 与根 `.env`，不再硬编码 `C:/Users/ZZY/...` 与 `sk-...`
- 模型调用走 `make_http_client()`，忽略系统代理（与 B 的处理一致）
- 支持 `--resume`（已评过的 run_id+question_id+method+model_id 跳过）与 `--workers` 并发
- 增加失败落盘：解析失败也写一条 status=error 的记录，不再静默丢弃

用法（仓库根执行）：

    python -m experiments.KEO.core.evaluation.run_eval --limit 6          # 冒烟
    python -m experiments.KEO.core.evaluation.run_eval                    # 全量 Demo
    python -m experiments.KEO.core.evaluation.run_eval --resume --workers 3
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI

from experiments.KEO.configs import config
from experiments.KEO.models.http_client import make_http_client

DIMS_CASE = ["类型判定", "证据指向", "措施正确性", "可执行性", "完整性"]
DIMS_GLOBAL = ["综合质量", "模式识别", "决策价值", "全局视角", "主题识别"]

RUBRIC_CASE = """
- 类型判定：5分完全准确；4分基本准确但表述略非标；3分大类正确但不精确；2分类别错误但有沾边；1分完全判定错误。
- 证据指向：5分精准提取核心细节（金额、平台）；4分提取了大部分关键点；3分有证据但遗漏关键细节；2分证据模糊笼统；1分无证据或胡编乱造。
- 措施正确性：5分措施完全针对该诈骗痛点；4分措施较合理；3分措施宽泛；2分措施部分偏离；1分措施错误。
- 可执行性：5分给出了具体可操作步骤；4分步骤较清晰；3分建议空泛；2分难以落地；1分毫无可执行性。
- 完整性：5分包含事前防范与事后补救；4分具备大部分要素；3分只有事前或只有事后；2分内容残缺；1分完全缺失。
"""

RUBRIC_GLOBAL = """
- 综合质量：5分表示跨案例综合极其完美；4分表示综合较好但略有瑕疵；3分表示中规中矩；2分表示综合能力较弱；1分表示完全没有综合。
- 模式识别：5分表示准确提炼出复杂关系或趋势；3分表示只能识别简单模式；1分表示未能识别出任何模式。
- 决策价值：5分表示对反诈实战有极高指导价值；3分表示有一定参考价值；1分表示毫无实际用处。
- 全局视角：5分表示基于整个数据集全局洞察；3分表示仅提及部分数据；1分表示仅凭主观臆断。
- 主题识别：5分表示清晰聚焦核心主题；3分表示主题略有跑偏；1分表示完全答非所问。
"""


def build_prompt(question: str, gold: str | None, answer: str, question_type: str) -> str:
    rubric = RUBRIC_GLOBAL if question_type == "global_analysis" else RUBRIC_CASE
    dims = DIMS_GLOBAL if question_type == "global_analysis" else DIMS_CASE
    dim_text = "、".join(dims)
    return f"""你是一名专业的警务反诈研判专家，现在担任阅卷考官。请严格根据【案情原文/问题】和【标准真实类别】，对【考生答卷】进行客观打分。

【问题/案情原文】
{question}

【标准真实类别（底牌）】
{gold if gold else "全局分析题（无单一标准类别）"}

【考生答卷】
{answer}

【评分细则】（注意：所有维度的分数必须在 1 到 5 之间，严禁出现 0 分。请综合参考以下描述给出整数）：
{rubric}

【输出格式要求】
请严格只输出合法的 JSON 格式，不要使用任何 Markdown 代码块（不要加 ```json），不要包含任何多余的解释文字。格式如下：
{{
  "scores": {{{", ".join(f'"{d}": 0' for d in dims)}}},
  "reason": "用一句话简短说明扣分或加分原因"
}}
（注意：scores 里的键名请严格使用：{dim_text}。）
"""


def parse_response(text: str, dims: list[str]) -> tuple[dict | None, str]:
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    try:
        payload = json.loads(cleaned)
        raw_scores = payload.get("scores") or {}
        scores = {}
        for dim in dims:
            value = raw_scores.get(dim)
            if value is None:
                return None, f"缺少维度 {dim}"
            value = int(value)
            if not 1 <= value <= 5:
                return None, f"维度 {dim} 分数越界 {value}"
            scores[dim] = value
        return scores, str(payload.get("reason", ""))
    except Exception as exc:  # noqa: BLE001
        return None, f"解析失败 {exc}：{cleaned[:120]}"


def score_one(client: OpenAI, model: str, question: str, gold: str | None,
              answer: str, question_type: str) -> tuple[dict | None, str]:
    dims = DIMS_GLOBAL if question_type == "global_analysis" else DIMS_CASE
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": build_prompt(question, gold, answer, question_type)}],
        temperature=0.1,
    )
    return parse_response(response.choices[0].message.content or "", dims)


def read_jsonl(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="D：逐条五维评分（格式 1.6）")
    parser.add_argument("--questions", type=Path, default=config.D_QUESTION_DEMO)
    parser.add_argument("--metadata", type=Path, default=config.QUESTION_METADATA)
    parser.add_argument("--answers", type=Path, default=config.answers_path())
    parser.add_argument("--out", type=Path, default=config.RESULT_ROOT / "evaluations.jsonl")
    parser.add_argument("--model", default="Qwen/Qwen2.5-32B-Instruct", help="评判模型")
    parser.add_argument("--limit", type=int, default=0, help="只评前 N 条回答（冒烟）")
    parser.add_argument("--workers", type=int, default=3, help="并发请求数")
    parser.add_argument("--resume", action="store_true", help="跳过已评过的记录")
    args = parser.parse_args()

    questions = {row["question_id"]: row for row in read_jsonl(args.questions)}
    metadatas = {row["question_id"]: row.get("gold_category") for row in read_jsonl(args.metadata)}
    answers = read_jsonl(args.answers)
    if args.limit:
        answers = answers[: args.limit]

    done: set[tuple] = set()
    existing: list[dict] = []
    if args.resume and args.out.exists():
        existing = read_jsonl(args.out)
        done = {(r["run_id"], r["question_id"], r["method"], r["model_id"]) for r in existing}
        print(f"续评：已有 {len(done):,} 条评分记录")

    todo = []
    for ans in answers:
        key = (ans.get("run_id"), ans.get("question_id"), ans.get("method"), ans.get("model_id"))
        if key in done:
            continue
        question = questions.get(ans.get("question_id"))
        if question is None:
            print(f"  跳过 {ans.get('question_id')}：题库里没有这道题")
            continue
        todo.append((ans, question))

    print(f"待评 {len(todo):,} 条  评判模型 {args.model}  并发 {args.workers}")
    client = OpenAI(api_key=config.require_api_key(), base_url=config.BASE_URL,
                    http_client=make_http_client(), max_retries=config.MAX_RETRIES)

    started = time.perf_counter()
    results: list[dict] = []
    finished = 0

    def work(item):
        ans, question = item
        qid = ans["question_id"]
        qtype = question.get("question_type", "case_judgment")
        try:
            scores, reason = score_one(client, args.model, question.get("question", ""),
                                       metadatas.get(qid), ans.get("answer_text", ""), qtype)
        except Exception as exc:  # noqa: BLE001
            scores, reason = None, f"调用失败 {exc}"
        record = {
            "run_id": ans.get("run_id", "unknown"),
            "question_id": qid,
            "method": ans.get("method", "unknown"),
            "model_id": ans.get("model_id", "unknown"),
            "evaluator_id": args.model,
            "scores": scores,
            "reason": reason,
            "status": "ok" if scores else "error",
            "error": None if scores else {"type": "EVALUATION_ERROR", "message": reason},
        }
        return record

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(work, item) for item in todo]
        for future in as_completed(futures):
            results.append(future.result())
            finished += 1
            if finished % 20 == 0:
                rate = finished / max(time.perf_counter() - started, 1e-6)
                print(f"  {finished:,}/{len(todo):,}  {rate:.2f} 条/秒", flush=True)

    merged = {(r["run_id"], r["question_id"], r["method"], r["model_id"]): r for r in existing}
    merged.update({(r["run_id"], r["question_id"], r["method"], r["model_id"]): r for r in results})
    final = [merged[key] for key in sorted(merged)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for record in final:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    ok = sum(1 for r in final if r["status"] == "ok")
    print(f"\n已写入 {args.out}（{len(final):,} 条，其中评分成功 {ok:,}）")
    print(f"耗时 {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
