import json
import os

from experiments.KEO.configs import config

# ================= 绝对路径配置区 =================
EVAL_CASES_FILE = config.EVAL_CASES_PATH
EVAL_IDS_FILE = config.EVAL_IDS_PATH
# 自行生成的题库单独存放，避免覆盖 D 已交付的正式题库。
GENERATED_DIR = config.QUESTION_ROOT / "generated"

# 统一的输出路径
QUESTIONS_OUT = GENERATED_DIR / "questions.jsonl"
METADATA_OUT = GENERATED_DIR / "question_metadata.jsonl"

def generate_questions():
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    print("正在读取名单和案件数据...")
    
    # 1. 读取全部测试集名单
    with open(EVAL_IDS_FILE, "r", encoding="utf-8") as f:
        eval_ids = [line.strip() for line in f if line.strip()]

    # 2. 建立案件字典
    cases_dict = {}
    with open(EVAL_CASES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            record = json.loads(line)
            cases_dict[record["source_id"]] = record

    print(f"正在生成 {len(eval_ids)} 道全量测试题（请稍候）...")
    
    # 3. 生成题目与底牌
    with open(QUESTIONS_OUT, 'w', encoding='utf-8') as fq, \
         open(METADATA_OUT, 'w', encoding='utf-8') as fm:
        
        q_num = 1
        for case_id in eval_ids:
            if case_id not in cases_dict: continue
                
            case = cases_dict[case_id]
            text = case["text"] 
            category = case["category"]
            
            # 统一使用 5 位编号（q00001 起）
            q_id = f"q{q_num:05d}"
            
            question_data = {
                "question_id": q_id,
                "question_type": "case_judgment",
                "question": f"{text}\n请判断该案属于哪种诈骗类型？指出关键证据，并给出防范建议。"
            }
            metadata_data = {
                "question_id": q_id,
                "origin_source_ids": [case_id],
                "gold_category": category
            }
            fq.write(json.dumps(question_data, ensure_ascii=False) + "\n")
            fm.write(json.dumps(metadata_data, ensure_ascii=False) + "\n")
            q_num += 1

        # 4. 追加 3 道全局分析题
        global_qs = [
            {"id": f"q{q_num:05d}", "question": "根据所有案件记录，哪种诈骗类型造成的总损失金额最高？该类诈骗的主要受害群体特征是什么？请结合具体案例数据说明。"},
            {"id": f"q{q_num+1:05d}", "question": "在所有诈骗案件中，诈骗分子最常使用哪些社交软件、平台或APP作为引流工具？针对中老年群体，最常见的引流渠道和诈骗类型是什么组合？"},
            {"id": f"q{q_num+2:05d}", "question": "请专门针对『刷单返利类』诈骗进行统计：此类案件的平均损失金额是多少？诈骗分子通常要求受害人在哪些特定平台或APP进行操作？其典型的话术流程是什么？"}
        ]

        for gq in global_qs:
            q_data = {"question_id": gq["id"], "question_type": "global_analysis", "question": gq["question"]}
            m_data = {"question_id": gq["id"], "origin_source_ids": [], "gold_category": None}
            fq.write(json.dumps(q_data, ensure_ascii=False) + "\n")
            fm.write(json.dumps(m_data, ensure_ascii=False) + "\n")

    print("🎉 全量题库生成完毕！")
    print(f"✅ 题目已保存至：{QUESTIONS_OUT}")
    print(f"✅ 底牌已保存至：{METADATA_OUT}")

if __name__ == "__main__":
    generate_questions()