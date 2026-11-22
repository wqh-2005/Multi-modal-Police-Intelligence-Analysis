"""D 的成对盲测：从配置读取，随机化 A/B 位置，记录失败，支持续跑。"""
from __future__ import annotations

import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations
from pathlib import Path

from openai import OpenAI

from experiments.KEO.configs import config
from experiments.KEO.models.http_client import make_http_client


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def call_llm_for_pairwise(client: OpenAI, model: str, question: str, answer_a: str, answer_b: str) -> tuple[str | None, str]:
    """调用大模型进行成对比较"""
    prompt = f"""
    你是一名专业的警务反诈研判专家。请阅读【案情原文】，然后对比【回答 A】和【回答 B】的质量。
    
    【案情原文】
    {question}
    
    【回答 A】
    {answer_a}
    
    【回答 B】
    {answer_b}
    
    【评判标准】
    请从三个维度综合评估：
    1. 诈骗类型判断谁更准确？
    2. 证据提取谁更贴合案情？
    3. 防范建议谁更有可执行性？
    
    请选择表现更好的一方，并给出理由。
    
    【输出格式】
    请严格只输出合法的 JSON 格式，不要任何废话：
    {{
      "winner": "a" 或 "b" 或 "tie",
      "reason": "简短说明为什么选它"
    }}
    """
    
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    
    result_text = response.choices[0].message.content
    result_text = result_text.replace("```json", "").replace("```", "").strip()
    try:
        result_json = json.loads(result_text)
        return result_json["winner"], result_json["reason"]
    except Exception as e:
        print(f"❌ 解析模型返回失败: {e}")
        return None, f"解析失败：{result_text[:100]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="D：成对盲测（格式 1.6）")
    parser.add_argument("--questions", type=Path, default=config.D_QUESTION_DEMO)
    parser.add_argument("--answers", type=Path, default=config.answers_path())
    parser.add_argument("--out", type=Path, default=config.RESULT_ROOT / "pairwise_evaluations.jsonl")
    parser.add_argument("--model", default="Qwen/Qwen2.5-32B-Instruct", help="评判模型")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 道题的对比")
    parser.add_argument("--workers", type=int, default=3, help="并发请求数")
    parser.add_argument("--resume", action="store_true", help="跳过已成功对比的记录")
    args = parser.parse_args()
    
    # D-06: 加入版本标识
    evaluator = args.model + "-v2-D06"

    # 1. 读取数据并验证
    questions = {row["question_id"]: row for row in read_jsonl(args.questions)}
    answers = read_jsonl(args.answers)
    
    if args.limit:
        answers = answers[:args.limit]

    # 按 run_id + model_id 分组，禁止跨批次混比
    grouped_answers = {}
    for ans in answers:
        rid = ans.get("run_id")
        mid = ans.get("model_id")
        key = (rid, mid)
        if key not in grouped_answers:
            grouped_answers[key] = {}
        
        qid = ans.get("question_id")
        if qid not in grouped_answers[key]:
            grouped_answers[key][qid] = {}
        grouped_answers[key][qid][ans.get("method")] = ans

    # 2. 生成对比任务
    tasks = []
    for (rid, mid), q_dict in grouped_answers.items():
        for qid, methods_dict in q_dict.items():
            if qid not in questions: continue
            
            available_methods = list(methods_dict.keys())
            if len(available_methods) < 2: continue
            
            # 生成两两组合 (direct vs text, direct vs graph, text vs graph)
            for method_a, method_b in combinations(available_methods, 2):
                tasks.append((rid, mid, qid, method_a, method_b))

    # 3. 续跑机制
    done = set()
    existing = []
    if args.resume and args.out.exists():
        existing = read_jsonl(args.out)
        done = {(r["run_id"], r["model_id"], r["question_id"], r["method_a"], r["method_b"]) 
                for r in existing if r.get("status") == "ok"}
        print(f"续评：已有 {len(done)} 条成功盲测记录")

    todo_tasks = [t for t in tasks if (t[0], t[1], t[2], t[3], t[4]) not in done]

    print(f"待评 {len(todo_tasks)} 组对比  评判模型 {args.model}  并发 {args.workers}")
    client = OpenAI(api_key=config.require_api_key(), base_url=config.BASE_URL,
                    http_client=make_http_client(), max_retries=config.MAX_RETRIES)

    # 4. 执行并发对比
    started = time.perf_counter()
    results = []
    
    def work(task):
        rid, mid, qid, method_a, method_b = task
        ans_a = grouped_answers[(rid, mid)][qid][method_a]
        ans_b = grouped_answers[(rid, mid)][qid][method_b]
        
        # D-06: 随机交换 A/B 位置
        is_swapped = random.choice([True, False])
        if is_swapped:
            prompt_a, prompt_b = ans_b.get("answer_text", ""), ans_a.get("answer_text", "")
        else:
            prompt_a, prompt_b = ans_a.get("answer_text", ""), ans_b.get("answer_text", "")
            
        try:
            winner_prompt, reason = call_llm_for_pairwise(
                client, args.model, questions[qid].get("question", ""), prompt_a, prompt_b
            )
            
            # 如果交换过，要把 winner 还原回来
            final_winner = winner_prompt
            if winner_prompt and winner_prompt in ["a", "b"]:
                if is_swapped:
                    final_winner = "b" if winner_prompt == "a" else "a"
                    
            status = "ok" if final_winner else "error"
        except Exception as exc:
            final_winner, reason, status = None, f"调用失败 {exc}", "error"

        record = {
            "run_id": rid,
            "question_id": qid,
            "model_id": mid,
            "evaluator_id": evaluator,
            "method_a": method_a,
            "method_b": method_b,
            "winner": final_winner,
            "reason": reason,
            "status": status,
            "error": None if status == "ok" else {"type": "EVALUATION_ERROR", "message": reason}
        }
        return record

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(work, t) for t in todo_tasks]
        for future in as_completed(futures):
            results.append(future.result())

    # 5. 合并保存
    merged = {(r["run_id"], r["model_id"], r["question_id"], r["method_a"], r["method_b"]): r for r in existing}
    merged.update({(r["run_id"], r["model_id"], r["question_id"], r["method_a"], r["method_b"]): r for r in results})
    final = [merged[k] for k in sorted(merged)]
    
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for record in final:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    ok = sum(1 for r in final if r["status"] == "ok")
    print(f"\n已写入 {args.out}（{len(final)} 条，成功 {ok} 条）")
    print(f"耗时 {time.perf_counter() - started:.1f}s")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())