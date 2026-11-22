"""D-08：独立测试集统计（Accuracy/Macro-F1/支持数/失败率/耗时/配对检验/置信区间）。"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats


def read_jsonl(path):
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def macro_f1(gold_list, pred_list):
    from collections import Counter
    classes = set(gold_list) | set(pred_list)
    total = 0.0
    for c in classes:
        tp = sum(1 for g, p in zip(gold_list, pred_list) if g == c and p == c)
        fp = sum(1 for g, p in zip(gold_list, pred_list) if g != c and p == c)
        fn = sum(1 for g, p in zip(gold_list, pred_list) if g == c and p != c)
        prec = tp / (tp + fp) if tp + fp else 0
        rec = tp / (tp + fn) if tp + fn else 0
        total += (2 * prec * rec / (prec + rec)) if prec + rec else 0
    return total / len(classes) if classes else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--evaluations", required=True)
    ap.add_argument("--out", default="stats_test.json")
    args = ap.parse_args()

    gold = {}   # question_id -> gold_category
    for row in read_jsonl(args.metadata):
        qid = row["question_id"]
        gold[qid] = row.get("gold_category")

    # 校验非全局题（gold 为 None 的不算分类）
    valid = {q: g for q, g in gold.items() if g is not None}

    m_stat = defaultdict(lambda: {"total": 0, "correct": 0, "fail": 0, "times": [], "gold": [], "pred": []})
    for row in read_jsonl(args.answers):
        m = row.get("method", "unknown")
        qid = row["question_id"]
        if row.get("status") != "ok":
            m_stat[m]["fail"] += 1
            continue
        ret = row.get("retrieval", {})
        if ret.get("elapsed_ms"):
            m_stat[m]["times"].append(ret["elapsed_ms"])
        if qid in valid:
            m_stat[m]["total"] += 1
            m_stat[m]["gold"].append(valid[qid])
            m_stat[m]["pred"].append(row.get("predicted_category"))
            if valid[qid] == row.get("predicted_category"):
                m_stat[m]["correct"] += 1

    print("=" * 60)
    print(f"{'方法':<12}{'Accuracy':<10}{'Macro-F1':<10}{'支持数':<8}{'失败率':<10}{'平均耗时ms'}")
    print("-" * 60)
    for m in sorted(m_stat):
        s = m_stat[m]
        total = s["total"]
        acc = s["correct"] / total if total else 0
        f1 = macro_f1(s["gold"], s["pred"]) if total else 0
        supp_counts = defaultdict(int)
        for g in s["gold"]:
            supp_counts[g] += 1
        fail_rate = s["fail"] / (total + s["fail"]) if (total + s["fail"]) else 0
        avg_t = np.mean(s["times"]) if s["times"] else 0
        print(f"{m:<12}{acc*100:>6.2f}%  {f1:>8.4f}  {total:>6}  {fail_rate*100:>6.2f}%  {avg_t:>8.1f}")

    print("\n类别支持数（按 method）:")
    for m in sorted(m_stat):
        supp = defaultdict(int)
        for g in m_stat[m]["gold"]:
            supp[g] += 1
        print(f"  {m}: {dict(supp)}")

    # 配对检验：text_rag vs graph_rag 同一批题的总分
    scores = defaultdict(dict)  # {qid: {method: 总分}}
    for row in read_jsonl(args.evaluations):
        if row.get("status") == "ok" and row.get("scores"):
            scores[row["question_id"]][row["method"]] = sum(row["scores"].values())

    pairs_t, pairs_g = [], []
    for qid, md in scores.items():
        if "text_rag" in md and "graph_rag" in md:
            pairs_t.append(md["text_rag"])
            pairs_g.append(md["graph_rag"])

    if len(pairs_t) >= 5:
        t_arr, g_arr = np.array(pairs_t), np.array(pairs_g)
        diff = g_arr - t_arr
        stat, p = stats.wilcoxon(g_arr, t_arr)
        rng = np.random.default_rng(42)
        boots = [np.mean(rng.choice(diff, size=len(diff), replace=True)) for _ in range(10000)]
        lo, hi = np.percentile(boots, [2.5, 97.5])
        print(f"\n配对检验 text_rag vs graph_rag:")
        print(f"  平均分差（graph - text）: {diff.mean():.2f}")
        print(f"  95% 置信区间: [{lo:.2f}, {hi:.2f}]")
        print(f"  Wilcoxon p: {p:.4f}")
        if p < 0.05 and lo > 0:
            print("  ✅ 差异达到统计学显著")
        else:
            print("  ⚠️ 差异未达显著，不能宣称'显著提升'")
    else:
        print(f"\n配对检验样本不足（{len(pairs_t)} 组，需≥5）")

    # 保存
    out = {
        "methods": {m: {"acc": ..., "macro_f1": ...} for m in m_stat}
    }

if __name__ == "__main__":
    main()