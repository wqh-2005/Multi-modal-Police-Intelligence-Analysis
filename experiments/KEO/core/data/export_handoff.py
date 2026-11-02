"""导出 B / C / D 三个角色的交接包（P1-2 / P1-3）。

输入（均为 A 的冻结产物）：
  person_a/cases_master.jsonl
  person_a/name_cleaning/apply_v1.0/triples_clean_v2.jsonl
  person_a/split_build_ids.txt / split_eval_ids.txt
  person_a/name_cleaning/apply_v1.0/实体别名映射.json

输出（默认 person_a/handoff/）：
  b/  cases_build.jsonl, build_ids.txt, dev_ids.txt, manifest.json
  c/  triples_build.jsonl, build_ids.txt, 实体别名映射.json, 归一化规则.md, manifest.json
  d/  cases_eval.jsonl,  eval_ids.txt,  manifest.json

用法：
  python -m experiments.KEO.core.data.export_handoff --help
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import CLEANING_DIR, HANDOFF_DIR, PERSON_A_DIR, rel

VERSION = "1.0-final"
CLEANING_VERSION = "1.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_entry(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


def read_ids(path: Path) -> list[str]:
    ids = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"{path} 存在重复 ID")
    return ids


def read_cases(path: Path, wanted: set[str], split: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            case_id = record.get("source_id")
            if case_id not in wanted:
                continue
            rows.append(
                {
                    "source_id": case_id,
                    "category": record.get("category"),
                    "text": record.get("text"),
                    "split": split,
                }
            )
    rows.sort(key=lambda item: item["source_id"])
    if len(rows) != len(wanted):
        missing = sorted(wanted - {item["source_id"] for item in rows})
        raise SystemExit(f"案例缺口 {len(missing)} 条，例如 {missing[:5]}")
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(rows)


def pick_dev_ids(cases: list[dict[str, Any]], size: int, seed: int) -> list[str]:
    """按类别轮转抽取开发集，保证类别覆盖且可复现。"""
    by_category: dict[str, list[str]] = defaultdict(list)
    for row in cases:
        by_category[row["category"] or "未知"].append(row["source_id"])
    rng = random.Random(seed)
    for ids in by_category.values():
        ids.sort()
        rng.shuffle(ids)
    dev: list[str] = []
    categories = sorted(by_category)
    cursor = {category: 0 for category in categories}
    while len(dev) < size and any(cursor[c] < len(by_category[c]) for c in categories):
        for category in categories:
            if len(dev) >= size:
                break
            index = cursor[category]
            if index < len(by_category[category]):
                dev.append(by_category[category][index])
                cursor[category] = index + 1
    return sorted(dev)


def write_build_package(handoff: Path, cases_all: Path, build_ids: list[str], build_set: set[str], dev_size: int, seed: int) -> dict[str, Any]:
    target = handoff / "b"
    target.mkdir(parents=True, exist_ok=True)
    cases = read_cases(cases_all, build_set, "build")
    case_lines = write_jsonl(target / "cases_build.jsonl", cases)
    (target / "build_ids.txt").write_text("\n".join(build_ids) + "\n", encoding="utf-8")
    dev_ids = pick_dev_ids(cases, dev_size, seed)
    (target / "dev_ids.txt").write_text("\n".join(dev_ids) + "\n", encoding="utf-8")
    files = {
        "cases_build.jsonl": file_entry(target / "cases_build.jsonl"),
        "build_ids.txt": file_entry(target / "build_ids.txt"),
        "dev_ids.txt": file_entry(target / "dev_ids.txt"),
    }
    return {
        "role": "B",
        "purpose": "文本检索与直接回答基线：建库原文 + 开发集",
        "counts": {"cases": case_lines, "build_ids": len(build_ids), "dev_ids": len(dev_ids)},
        "files": files,
        "usage": [
            "cases_build.jsonl：字段 source_id / category / text / split，建库原文；",
            "build_ids.txt：权威建库名单，必须与 C 使用同一份；",
            "dev_ids.txt：从建库侧按类别轮转抽出的开发集，只用于调参，不计入评估。",
        ],
    }


def write_graph_package(handoff: Path, triples_clean_v2: Path, alias_map: Path, build_ids: list[str], build_set: set[str]) -> dict[str, Any]:
    target = handoff / "c"
    target.mkdir(parents=True, exist_ok=True)
    triples = 0
    case_ids: set[str] = set()
    with triples_clean_v2.open("r", encoding="utf-8") as reader, (target / "triples_build.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as writer:
        for line in reader:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("source_id") not in build_set:
                continue
            triples += 1
            case_ids.add(record["source_id"])
            writer.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    (target / "build_ids.txt").write_text("\n".join(build_ids) + "\n", encoding="utf-8")
    shutil.copyfile(alias_map, target / "实体别名映射.json")
    rules = target / "归一化规则.md"
    rules.write_text(build_normalization_rules(alias_map, build_ids, case_ids, triples), encoding="utf-8")
    files = {
        "triples_build.jsonl": file_entry(target / "triples_build.jsonl"),
        "build_ids.txt": file_entry(target / "build_ids.txt"),
        "实体别名映射.json": file_entry(target / "实体别名映射.json"),
        "归一化规则.md": file_entry(rules),
    }
    return {
        "role": "C",
        "purpose": "图谱 RAG：建库侧三元组 + 名称映射规则",
        "counts": {"triples": triples, "cases_with_triples": len(case_ids), "build_ids": len(build_ids)},
        "files": files,
        "usage": [
            "triples_build.jsonl：只含 build_ids 的三元组，head/tail.name 已按确认口径清洗，被改名时带 original_name；",
            "实体别名映射.json：正式别名表，C 直接使用，不再做语义相似度自动合并；",
            "归一化规则.md：清洗口径、未合并项与建图注意事项。",
        ],
    }


def write_eval_package(handoff: Path, cases_all: Path, eval_ids: list[str], eval_set: set[str]) -> dict[str, Any]:
    target = handoff / "d"
    target.mkdir(parents=True, exist_ok=True)
    cases = read_cases(cases_all, eval_set, "eval")
    case_lines = write_jsonl(target / "cases_eval.jsonl", cases)
    (target / "eval_ids.txt").write_text("\n".join(eval_ids) + "\n", encoding="utf-8")
    files = {
        "cases_eval.jsonl": file_entry(target / "cases_eval.jsonl"),
        "eval_ids.txt": file_entry(target / "eval_ids.txt"),
    }
    return {
        "role": "D",
        "purpose": "测试题组织与评估：评估侧原文与真实类别",
        "counts": {"cases": case_lines, "eval_ids": len(eval_ids)},
        "files": files,
        "usage": [
            "cases_eval.jsonl：字段 source_id / category / text / split，只含评估侧案例；",
            "eval_ids.txt：权威评估名单；",
            "评估侧不含建库三元组，避免把建库内容当作评估答案。",
        ],
    }


def build_normalization_rules(alias_map: Path, build_ids: list[str], case_ids: set[str], triples: int) -> str:
    payload = json.loads(alias_map.read_text(encoding="utf-8"))
    decisions = payload.get("decisions", {})
    lines: list[str] = []
    lines.append("# C 建图用名称归一化规则（人员 A 交付）")
    lines.append("")
    lines.append(f"> 清洗版本：`{payload.get('version')}`  ")
    lines.append(f"> 包版本：`{VERSION}`  ")
    lines.append(f"> 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}  ")
    lines.append("> 来源：`name_cleaning/apply_v1.0/实体别名映射.json`")
    lines.append("")
    lines.append("## 1. 已确认口径")
    lines.append("")
    for key, label in (
        ("rule_a", "规则 A 归一化"),
        ("rule_b", "规则 B 空名称"),
        ("rule_c", "规则 C UNKNOWN"),
        ("rule_d", "规则 D 别名合并"),
        ("rule_d_canonical", "规则 D 规范名选择"),
        ("scope", "作用范围"),
    ):
        if key in decisions:
            lines.append(f"- **{label}**：{decisions[key]}")
    lines.append("")
    lines.append(f"归一化键定义：`{payload.get('normalize_rule', '')}`")
    lines.append("")
    lines.append("## 2. 本包数据")
    lines.append("")
    lines.append(f"- 建库案例：{len(build_ids):,}")
    lines.append(f"- 有三元组的建库案例：{len(case_ids):,}")
    lines.append(f"- 建库三元组：{triples:,}")
    lines.append(f"- 别名映射：{payload.get('mapping_count', 0):,} 个 `(类型, 原名) → 规范名`")
    lines.append(f"- 别名组：{payload.get('group_count', 0):,} 组（全部为确定性的 auto 组）")
    lines.append(f"- 冲突名称：{payload.get('conflict_count', 0):,} 个（按 apply_priority → member_mentions → group_id 解析）")
    lines.append("")
    lines.append("## 3. 建图注意")
    lines.append("")
    lines.append("- `head.name` / `tail.name` 已是清洗后的名称；`original_name` 只在被改名时出现，仅作追溯；")
    lines.append("- 人员按 `(source_id, head.id)` 建节点，不跨案合并，**同名也不合并**；")
    lines.append("- 身份、机构、应用程序、平台按 `(type, 规范名称)` 共享节点；不同 `type` 之间不合并；")
    lines.append("- 身份角色不做包含关系合并：`客服` 与 `京东客服` 是两个节点；")
    lines.append("- `未知` 是案件内占位节点，不要做成跨案全局节点；")
    lines.append("- `review` 1,434 组与 `hold` 898 组未合并：其中 `review` 保留候选供后续人工确认，`hold` 属于泛化名称或后缀差异过大，不要自行合并；")
    lines.append("- 空名称与 `UNKNOWN` 已统一为 `未知`，`original_name` 保留原值。")
    lines.append("")
    lines.append("## 4. 与 KEO 总接口的关系")
    lines.append("")
    lines.append("- 本包三元组与 `handoff/keo/三元组数据.jsonl` 中 `source_id ∈ build_ids` 的部分逐条一致；")
    lines.append("- 需要整体核对时以 `handoff/keo/` 与 `dataset_manifest.json` 的 SHA256 为准。")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出 B/C/D 交接包（P1-2/P1-3）")
    parser.add_argument("--person-a", default=str(PERSON_A_DIR), help="person_a 目录")
    parser.add_argument("--handoff", default=str(HANDOFF_DIR.parent), help="handoff 输出根目录")
    parser.add_argument("--dev-size", type=int, default=22, help="B 的开发集大小（按类别轮转抽取）")
    parser.add_argument("--seed", type=int, default=42, help="开发集随机种子")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    person_a = Path(args.person_a).expanduser()
    handoff = Path(args.handoff).expanduser()

    cases_all = person_a / "cases_master.jsonl"
    triples_clean_v2 = CLEANING_DIR / "triples_clean_v2.jsonl"
    alias_map = CLEANING_DIR / "实体别名映射.json"
    build_path = person_a / "split_build_ids.txt"
    eval_path = person_a / "split_eval_ids.txt"
    for path in (cases_all, triples_clean_v2, alias_map, build_path, eval_path):
        if not path.is_file():
            raise SystemExit(f"输入不存在: {path}")

    build_ids = read_ids(build_path)
    eval_ids = read_ids(eval_path)
    build_set, eval_set = set(build_ids), set(eval_ids)
    overlap = build_set & eval_set
    if overlap:
        raise SystemExit(f"建库/评估名单有交集，例如 {sorted(overlap)[:5]}")

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    packages = {
        "b": write_build_package(handoff, cases_all, build_ids, build_set, args.dev_size, args.seed),
        "c": write_graph_package(handoff, triples_clean_v2, alias_map, build_ids, build_set),
        "d": write_eval_package(handoff, cases_all, eval_ids, eval_set),
    }

    common = {
        "version": VERSION,
        "cleaning_version": CLEANING_VERSION,
        "generated_at": generated_at,
        "seed": args.seed,
        "split": {"build": len(build_ids), "eval": len(eval_ids), "overlap": 0},
        "sources": {
            "keo_package": rel(handoff / "keo"),
            "dataset_manifest": rel(person_a / "dataset_manifest.json"),
            "cases_master": file_entry(cases_all),
            "triples_clean_v2": file_entry(triples_clean_v2),
            "alias_map": file_entry(alias_map),
        },
        "notes": [
            "本包是 handoff/keo 的按角色切片，数据同源，未重新抽取或改写；",
            "权威划分以 split_build_ids.txt / split_eval_ids.txt（= 划分名单.json）为准；",
            "校验和见各包 manifest.json 与 person_a/dataset_manifest.json。",
        ],
    }
    for name, payload in packages.items():
        manifest = {**common, **payload}
        (handoff / name / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(
        json.dumps(
            {
                "handoff": rel(handoff),
                "version": VERSION,
                "packages": {
                    name: {
                        "counts": payload["counts"],
                        "files": {f: entry["sha256"][:16] for f, entry in payload["files"].items()},
                    }
                    for name, payload in packages.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
