"""D 的汇总报告：把评分（格式 1.6）与答卷（格式 1.4）合成一份可直接粘贴的 Markdown。

原脚本只打印平均总分与耗时，且路径写死。现在改为读 `config`，并按维度拆分：

- 五维平均分与总分（满分 25）
- 检索耗时与回答耗时
- 检索状态分布、带引用比例
- 分类指标（若 metrics.json 存在则一并引用）

用法（仓库根执行）：

    python -m experiments.KEO.core.evaluation.report
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from experiments.KEO.configs import config

DIMS_CASE = ["类型判定", "证据指向", "措施正确性", "可执行性", "完整性"]
DIMS_GLOBAL = ["综合质量", "模式识别", "决策价值", "全局视角", "主题识别"]


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="D：生成汇总报告")
    parser.add_argument("--evaluations", type=Path, default=config.RESULT_ROOT / "evaluations.jsonl")
    parser.add_argument("--answers", type=Path, default=config.answers_path())
    parser.add_argument("--metrics", type=Path, default=config.RESULT_ROOT / "metrics.json")
    parser.add_argument("--out", type=Path, default=config.RESULT_ROOT / "report.md")
    args = parser.parse_args()

    evaluations = read_jsonl(args.evaluations)
    answers = read_jsonl(args.answers)
    metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8")) if Path(args.metrics).exists() else {}

    # 评分统计：按方法汇总总分与各维度
    by_method: dict[str, list[dict]] = defaultdict(list)
    for row in evaluations:
        if row.get("status") == "ok" and row.get("scores"):
            by_method[row["method"]].append(row)

    # 答卷统计：耗时、检索状态、引用
    answer_stats: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "retrieval_ok": 0, "retrieval_empty": 0, "retrieval_error": 0,
        "cited": 0, "gen_ms": [], "ret_ms": [], "failed": 0,
    })
    for row in answers:
        method = row.get("method", "unknown")
        stat = answer_stats[method]
        stat["count"] += 1
        if row.get("status") != "ok":
            stat["failed"] += 1
        retrieval = row.get("retrieval") or {}
        status = retrieval.get("status")
        if status == "ok":
            stat["retrieval_ok"] += 1
        elif status == "empty":
            stat["retrieval_empty"] += 1
        elif status == "error":
            stat["retrieval_error"] += 1
        if row.get("cited_evidence_ids"):
            stat["cited"] += 1
        if row.get("generation_elapsed_ms"):
            stat["gen_ms"].append(row["generation_elapsed_ms"])
        if retrieval.get("elapsed_ms"):
            stat["ret_ms"].append(retrieval["elapsed_ms"])

    methods = sorted(set(by_method) | set(answer_stats))
    lines: list[str] = []
    lines.append("# KEO Demo 评测报告\n")
    lines.append(f"- 评分记录：{len(evaluations):,} 条")
    lines.append(f"- 答卷记录：{len(answers):,} 条")
    lines.append(f"- 方法：{', '.join(methods)}\n")

    lines.append("## 一、五维评分（满分 25）\n")
    lines.append("| 方法 | 已评条数 | 平均总分 | " + " | ".join(DIMS_CASE) + " |")
    lines.append("|---|---:|---:|" + "---:|" * len(DIMS_CASE))
    for method in methods:
        rows = by_method.get(method, [])
        if not rows:
            continue
        totals = [sum(r["scores"].values()) for r in rows]
        dim_values = {dim: [r["scores"].get(dim, 0) for r in rows] for dim in DIMS_CASE}
        dim_cells = " | ".join(f"{mean(dim_values[d]):.2f}" for d in DIMS_CASE)
        lines.append(f"| {method} | {len(rows)} | {mean(totals):.2f} | {dim_cells} |")

    lines.append("\n## 二、分类指标\n")
    if metrics:
        lines.append("| 方法 | 题数 | Accuracy | Macro-F1 |")
        lines.append("|---|---:|---:|---:|")
        for method in sorted(metrics):
            row = metrics[method]
            lines.append(f"| {method} | {row['total']} | {row['accuracy'] * 100:.2f}% | {row['macro_f1']:.4f} |")
    else:
        lines.append("未找到 metrics.json，先运行 `metrics.py`。")

    lines.append("\n## 三、运行情况\n")
    lines.append("| 方法 | 答卷 | 失败 | 检索 ok/empty/error | 带引用 | 平均生成耗时 | 平均检索耗时 |")
    lines.append("|---|---:|---:|---|---:|---:|---:|")
    for method in methods:
        stat = answer_stats.get(method)
        if not stat or not stat["count"]:
            continue
        lines.append(
            f"| {method} | {stat['count']} | {stat['failed']} | "
            f"{stat['retrieval_ok']}/{stat['retrieval_empty']}/{stat['retrieval_error']} | "
            f"{stat['cited']}/{stat['count']} | "
            f"{mean(stat['gen_ms']):.0f} ms | {mean(stat['ret_ms']):.0f} ms |"
        )

    lines.append("\n## 四、说明\n")
    lines.append("- 五维评分由评判模型给出，评分细则见 `core/evaluation/run_eval.py`。")
    lines.append("- Accuracy 与 Macro-F1 只用有金标的案例题；全局题不参与分类指标。")
    lines.append("- 金标 `question_metadata.jsonl` 由 A 的评估集按题干前缀反推，需 D 复核。")

    text = "\n".join(lines) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
