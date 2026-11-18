"""D 的分类指标：准确率、Macro-F1、按类别明细、错误清单。

原脚本只算 Accuracy 并打印，且路径写死。现在改为读 `config`，并把
Accuracy、Macro-F1 与逐类明细一并落盘，供报告引用。

Macro-F1 的意义：先按类别分别算 F1 再平均，防止大类刷分。本案刷单返利类占
34.5%，若只报 Accuracy，全猜刷单也能拿到不低的分数。

用法（仓库根执行）：

    python -m experiments.KEO.core.evaluation.metrics
    python -m experiments.KEO.core.evaluation.metrics --out output/results/metrics.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from experiments.KEO.configs import config


def read_jsonl(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def compute(answers: list[dict], gold: dict[str, str | None]) -> dict:
    """按方法统计 Accuracy 与 Macro-F1；只统计有金标的题。"""
    per_method: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "correct": 0,
        "confusion": Counter(),      # (真实, 预测) -> 次数
        "errors": [],
    })

    for row in answers:
        qid = row.get("question_id")
        truth = gold.get(qid)
        if truth is None:            # 全局题没有单一类别，不计入分类指标
            continue
        method = row.get("method", "unknown")
        pred = row.get("predicted_category")
        stat = per_method[method]
        stat["total"] += 1
        if pred == truth:
            stat["correct"] += 1
        else:
            stat["errors"].append({"question_id": qid, "true": truth, "pred": pred})
        stat["confusion"][(truth, pred)] += 1

    report: dict[str, dict] = {}
    for method, stat in per_method.items():
        total, correct = stat["total"], stat["correct"]
        # 逐类 F1：以真实类别为基准，统计该类的 TP/FP/FN
        classes = sorted({truth for truth, _ in stat["confusion"]})
        f1_scores = []
        per_class: dict[str, dict] = {}
        for cls in classes:
            tp = stat["confusion"].get((cls, cls), 0)
            fp = sum(n for (truth, pred), n in stat["confusion"].items()
                     if pred == cls and truth != cls)
            fn = sum(n for (truth, pred), n in stat["confusion"].items()
                     if truth == cls and pred != cls)
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
            f1_scores.append(f1)
            per_class[cls] = {
                "support": tp + fn, "tp": tp, "fp": fp, "fn": fn,
                "precision": round(precision, 4), "recall": round(recall, 4),
                "f1": round(f1, 4),
            }
        report[method] = {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 4) if total else 0.0,
            "macro_f1": round(sum(f1_scores) / len(f1_scores), 4) if f1_scores else 0.0,
            "classes": len(classes),
            "per_class": per_class,
            "errors": stat["errors"][:50],
            "error_count": len(stat["errors"]),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="D：分类准确率与 Macro-F1")
    parser.add_argument("--answers", type=Path, default=config.answers_path())
    parser.add_argument("--metadata", type=Path, default=config.QUESTION_METADATA)
    parser.add_argument("--out", type=Path, default=config.RESULT_ROOT / "metrics.json")
    args = parser.parse_args()

    if not args.answers.exists():
        raise FileNotFoundError(f"找不到答卷：{args.answers}")
    if not args.metadata.exists():
        raise FileNotFoundError(f"找不到金标：{args.metadata}")

    gold = {row["question_id"]: row.get("gold_category") for row in read_jsonl(args.metadata)}
    answers = read_jsonl(args.answers)
    report = compute(answers, gold)

    print(f"{'方法':<12} {'题数':>6} {'正确':>6} {'Accuracy':>10} {'Macro-F1':>10}")
    print("-" * 50)
    for method in sorted(report):
        row = report[method]
        print(f"{method:<12} {row['total']:>6} {row['correct']:>6} "
              f"{row['accuracy'] * 100:>9.2f}% {row['macro_f1']:>10.4f}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {args.out}")

    for method in sorted(report):
        row = report[method]
        if row["error_count"]:
            print(f"\n[{method}] 分类错误 {row['error_count']} 条，前 3 条：")
            for err in row["errors"][:3]:
                print(f"  {err['question_id']} 真实={err['true']} 预测={err['pred']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
