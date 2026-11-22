import argparse
import json
from pathlib import Path
import os
from itertools import combinations
from openai import OpenAI

from experiments.KEO.configs import config

# ================= 配置区 =================
API_KEY = "sk-..."  
BASE_URL = "https://api.siliconflow.cn/v1"   # 硅基流动
MODEL_NAME = "Qwen/Qwen2.5-32B-Instruct"

# 路径配置
QUESTIONS_FILE = config.D_QUESTION_DEMO
ANSWERS_FILE = config.answers_path()
OUTPUT_FILE = config.RESULT_ROOT / "pairwise_evaluations.jsonl"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

def call_llm_for_pairwise(question, answer_a, answer_b):
    """调用大模型进行成对比较（盲测）"""
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
        model=MODEL_NAME,
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
        return None, None

def main():
    parser = argparse.ArgumentParser(description="三种方法回答的成对比较")
    parser.add_argument("--questions", type=Path, default=QUESTIONS_FILE)
    parser.add_argument("--answers", type=Path, default=ANSWERS_FILE)
    parser.add_argument("--out", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # 1. 读取题库
    questions = {}
    with open(args.questions, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            data = json.loads(line)
            questions[data["question_id"]] = data["question"]

    # 2. 读取答卷并分组
    answers = []
    with open(args.answers, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            answers.append(json.loads(line))

    answer_pairs = {}
    for ans in answers:
        q_id = ans.get("question_id")
        method = ans.get("method")
        if q_id not in answer_pairs:
            answer_pairs[q_id] = {}
        answer_pairs[q_id][method] = ans

    print(f"成功加载 {len(answer_pairs)} 道题的答卷。开始进行全量两两盲测...")

    # 3. 开始成对比较（利用 itertools.combinations 自动生成所有两两组合）
    with open(args.out, "w", encoding="utf-8") as f_out:
        for q_id, methods_dict in answer_pairs.items():
            # 提取当前题目下有哪些方法
            available_methods = list(methods_dict.keys())
            
            # 如果这道题的方法少于2种，无法对比
            if len(available_methods) < 2:
                print(f"⚠️ 题目 {q_id} 只有 {len(available_methods)} 种方法的回答，跳过对比。")
                continue
            
            # 自动生成两两组合，例如：[('direct', 'text_rag'), ('direct', 'graph_rag'), ('text_rag', 'graph_rag')]
            method_combinations = list(combinations(available_methods, 2))
            
            for method_a, method_b in method_combinations:
                ans_a = methods_dict[method_a]
                ans_b = methods_dict[method_b]
                
                print(f"正在对比题目 {q_id} ({method_a} vs {method_b})...")
                
                winner, reason = call_llm_for_pairwise(
                    question=questions.get(q_id, ""),
                    answer_a=ans_a.get("answer_text", ""),
                    answer_b=ans_b.get("answer_text", "")
                )
                
                if winner is not None:
                    pair_data = {
                        "run_id": ans_a.get("run_id", "test_run"),
                        "question_id": q_id,
                        "model_id": ans_a.get("model_id", "unknown"),
                        "evaluator_id": MODEL_NAME,
                        "method_a": method_a,
                        "method_b": method_b,
                        "winner": winner,
                        "reason": reason,
                        "status": "ok",
                        "error": None
                    }
                    f_out.write(json.dumps(pair_data, ensure_ascii=False) + "\n")
                    print(f"✅ 对比完成 ({method_a} vs {method_b})，胜者: {winner}")

    print(f"\n🎉 成对比较全部完成！结果已保存至：{OUTPUT_FILE}")

if __name__ == "__main__":
    main()