import json
import os
from collections import defaultdict

# ================= 配置区 =================
BASE_DIR = "C:/Users/ZZY/Desktop/dc/Multi-modal-Police-Intelligence-Analysis/experiments/KEO/output/prepared_data"
EVALUATIONS_FILE = f"{BASE_DIR}/evaluations.jsonl"  
ANSWERS_FILE = f"{BASE_DIR}/answers.jsonl"     

def main():
    print("正在读取数据生成汇总报表...")
    
    # 1. 统计分数
    method_scores = defaultdict(list)
    if os.path.exists(EVALUATIONS_FILE):
        with open(EVALUATIONS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                data = json.loads(line)
                method = data.get("method")
                scores = data.get("scores", {})
                if scores:
                    method_scores[method].append(sum(scores.values()))

    # 2. 统计检索耗时
    method_time = defaultdict(list)
    if os.path.exists(ANSWERS_FILE):
        with open(ANSWERS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                data = json.loads(line)
                method = data.get("method")
                retrieval = data.get("retrieval", {})
                elapsed = retrieval.get("elapsed_ms", 0)
                if method:
                    method_time[method].append(elapsed)

    # 3. 打印表格
    all_methods = set(method_scores.keys()) | set(method_time.keys())
    
    print("\n" + "="*65)
    print(f"{'方法 (Method)':<15} | {'测试题数':<8} | {'平均得分(满分25)':<12} | {'平均检索耗时(ms)'}")
    print("-" * 65)
    
    for method in sorted(all_methods):
        scores = method_scores.get(method, [])
        times = method_time.get(method, [])
        
        avg_score = sum(scores) / len(scores) if scores else 0
        avg_time = sum(times) / len(times) if times else 0
        count = len(scores) if scores else len(times)
        
        if count > 0:
            print(f"{method:<15} | {count:<10} | {avg_score:<16.2f} | {avg_time:.2f}")
            
    print("="*65)
    print("📊 数据报表生成完毕。\n")

if __name__ == "__main__":
    main()