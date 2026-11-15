import json
import os
from openai import OpenAI

# ================= 配置区 =================
API_KEY = "sk-..."
BASE_URL = "https://api.siliconflow.cn/v1" # 硅基流动
MODEL_NAME = "Qwen/Qwen2.5-32B-Instruct" 

BASE_DIR = "C:/Users/ZZY/Desktop/dc/Multi-modal-Police-Intelligence-Analysis/experiments/KEO/output/prepared_data"
QUESTIONS_FILE = f"{BASE_DIR}/questions.jsonl"
METADATA_FILE = f"{BASE_DIR}/question_metadata.jsonl"
ANSWERS_FILE = f"{BASE_DIR}/answers.jsonl"  
OUTPUT_FILE = f"{BASE_DIR}/evaluations.jsonl"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

def call_llm_for_score(question, gold_category, student_answer, question_type="case_judgment"):
    """调用大模型进行打分，根据题型自动切换评分维度"""
    
    base_prompt = f"""
    你是一名专业的警务反诈研判专家，现在担任阅卷考官。请严格根据【案情原文/问题】和【标准真实类别】，对【考生答卷】进行客观打分。

    【问题/案情原文】
    {question}
    
    【标准真实类别（底牌）】
    {gold_category if gold_category else "全局分析题（无单一标准类别）"}
    
    【考生答卷】
    {student_answer}
    """
    
    if question_type == "global_analysis":
        rubric = """
    【评分细则】（注意：所有维度的分数必须在 1 到 5 之间，严禁出现 0 分。请综合参考以下描述给出整数）：
    - 综合质量：5分表示跨案例综合极其完美；4分表示综合较好但略有瑕疵；3分表示中规中矩；2分表示综合能力较弱；1分表示完全没有综合。
    - 模式识别：5分表示准确提炼出复杂关系或趋势；3分表示只能识别简单模式；1分表示未能识别出任何模式。
    - 决策价值：5分表示对反诈实战有极高指导价值；3分表示有一定参考价值；1分表示毫无实际用处。
    - 全局视角：5分表示基于整个数据集全局洞察；3分表示仅提及部分数据；1分表示仅凭主观臆断。
    - 主题识别：5分表示清晰聚焦核心主题；3分表示主题略有跑偏；1分表示完全答非所问。
    """
    else:
        rubric = """
    【评分细则】（注意：所有维度的分数必须在 1 到 5 之间，严禁出现 0 分。请综合参考以下描述给出整数）：
    - 类型判定：5分完全准确；4分基本准确但表述略非标；3分大类正确但不精确；2分类别错误但有沾边；1分完全判定错误。
    - 证据指向：5分精准提取核心细节（金额、平台）；4分提取了大部分关键点；3分有证据但遗漏关键细节；2分证据模糊笼统；1分无证据或胡编乱造。
    - 措施正确性：5分措施完全针对该诈骗痛点；4分措施较合理；3分措施宽泛；2分措施部分偏离；1分措施错误。
    - 可执行性：5分给出了具体可操作步骤；4分步骤较清晰；3分建议空泛；2分难以落地；1分毫无可执行性。
    - 完整性：5分包含事前防范与事后补救；4分具备大部分要素；3分只有事前或只有事后；2分内容残缺；1分完全缺失。
    """

    prompt = f"""
    {base_prompt}
    {rubric}
    
    【输出格式要求】
    请严格只输出合法的 JSON 格式，不要使用任何 Markdown 代码块（不要加 ```json），不要包含任何多余的解释文字。格式如下：
    {{
      "scores": {{"维度1名称": 0, "维度2名称": 0, "维度3名称": 0, "维度4名称": 0, "维度5名称": 0}},
      "reason": "用一句话简短说明扣分或加分原因"
    }}
    （注意：scores 里的键名，如果是全局题，请严格使用：综合质量、模式识别、决策价值、全局视角、主题识别；如果是案例题，请严格使用：类型判定、证据指向、措施正确性、可执行性、完整性。）
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
        return result_json["scores"], result_json["reason"]
    except Exception as e:
        print(f"❌ 解析模型返回失败: {e}")
        print(f"模型原始返回: {result_text}")
        return None, None

def main():
    # 1. 读取题库、底牌、答卷
    questions = {}
    q_types = {} # 
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            data = json.loads(line)
            questions[data["question_id"]] = data["question"]
            q_types[data["question_id"]] = data.get("question_type", "case_judgment")

    metadatas = {}
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            data = json.loads(line)
            metadatas[data["question_id"]] = data["gold_category"]

    answers = []
    with open(ANSWERS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            answers.append(json.loads(line))

    print(f"成功加载：{len(questions)} 道题，{len(metadatas)} 个底牌，{len(answers)} 份答卷。开始打分...")

    # 2. 遍历答卷，进行打分
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f_out:
        for idx, ans in enumerate(answers):
            q_id = ans.get("question_id")
            if q_id not in questions:
                print(f"⚠️ 找不到题目 {q_id}，跳过。")
                continue

            print(f"正在为第 {idx+1}/{len(answers)} 道题打分 (ID: {q_id})...")
            
            # 调用大模型打分
            scores, reason = call_llm_for_score(
                question=questions[q_id],
                gold_category=metadatas.get(q_id),
                student_answer=ans.get("answer_text", ""),
                question_type=q_types.get(q_id, "case_judgment")
            )

            if scores is None:
                continue 
            
            eval_data = {
                "run_id": ans.get("run_id", "test_run"),
                "question_id": q_id,
                "method": ans.get("method", "unknown"),
                "model_id": ans.get("model_id", "unknown"),
                "evaluator_id": MODEL_NAME,
                "scores": scores,
                "reason": reason,
                "status": "ok",
                "error": None
            }
            
            f_out.write(json.dumps(eval_data, ensure_ascii=False) + "\n")
            print(f"✅ {q_id} 打分完成：{scores}")

    print(f"\n🎉 全部打分完毕！结果已保存至：{OUTPUT_FILE}")

if __name__ == "__main__":
    main()