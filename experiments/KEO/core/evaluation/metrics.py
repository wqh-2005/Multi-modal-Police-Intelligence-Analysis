import json
import os

# ================= 配置区 =================
BASE_DIR = "C:/Users/ZZY/Desktop/dc/Multi-modal-Police-Intelligence-Analysis/experiments/KEO/output/prepared_data"
ANSWERS_FILE = f"{BASE_DIR}/answers.jsonl" 
METADATA_FILE = f"{BASE_DIR}/question_metadata.jsonl" 

def main():
    if not os.path.exists(ANSWERS_FILE):
        print(f"❌ 找不到答卷文件：{ANSWERS_FILE}")
        return
    if not os.path.exists(METADATA_FILE):
        print(f"❌ 找不到底牌文件：{METADATA_FILE}")
        return

    print("正在读取数据计算分类准确率...")
    
    # 1. 读取底牌，提取真实类别
    gold_labels = {}
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            data = json.loads(line)
            gold_labels[data["question_id"]] = data["gold_category"]

    # 2. 统计各方法的正确数和总数
    method_stats = {}
    # 用来记录错误的案例（供后续人工复核）
    error_cases = [] 

    with open(ANSWERS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            data = json.loads(line)
            q_id = data["question_id"]
            method = data["method"]
            pred_category = data.get("predicted_category", "")

            # 排除全局题（没有真实类别的不算准确率）
            true_category = gold_labels.get(q_id)
            if true_category is None:
                continue

            # 初始化该方法的统计字典
            if method not in method_stats:
                method_stats[method] = {"correct": 0, "total": 0}

            method_stats[method]["total"] += 1
            
            # 比对类别
            if pred_category == true_category:
                method_stats[method]["correct"] += 1
            else:
                # 记录错误案例
                error_cases.append({
                    "question_id": q_id,
                    "method": method,
                    "true": true_category,
                    "pred": pred_category
                })

    # 3. 打印分类指标结果
    print("\n" + "="*50)
    print(f"{'方法 (Method)':<15} | {'正确数':<8} | {'总数':<8} | {'分类准确率(Accuracy)'}")
    print("-" * 50)
    
    for method, stats in method_stats.items():
        if stats["total"] > 0:
            acc = (stats["correct"] / stats["total"]) * 100
            print(f"{method:<15} | {stats['correct']:<10} | {stats['total']:<8} | {acc:.2f}%")
            
    print("="*50)

    # 4. 打印错误案例（满足清单“按类别分析错误”要求）
    if error_cases:
        print(f"\n⚠️ 共发现 {len(error_cases)} 条分类错误案例（供人工复核参考）：")
        for err in error_cases[:5]: # 终端里只打印前5条防刷屏
            print(f"题目 {err['question_id']} | 方法 {err['method']} | 真实: {err['true']} | 预测: {err['pred']}")
        if len(error_cases) > 5:
            print("... (更多错误案例已记录在脚本中)")
    else:
        print("\n🎉 完美！没有发现分类错误案例。")

if __name__ == "__main__":
    main()