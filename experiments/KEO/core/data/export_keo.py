from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


from .paths import CLEANING_DIR, HANDOFF_DIR_V11, PERSON_A_DIR, REPO_ROOT, rel

DEFAULT_ROOT = REPO_ROOT
DEFAULT_PERSON_A = PERSON_A_DIR
DEFAULT_CASES = PERSON_A_DIR / "cases_master.jsonl"
DEFAULT_CLEANED_TRIPLES = CLEANING_DIR / "triples_clean_v2.jsonl"
DEFAULT_TRIPLES = (
    DEFAULT_CLEANED_TRIPLES if DEFAULT_CLEANED_TRIPLES.is_file() else PERSON_A_DIR / "triples_clean.jsonl"
)
DEFAULT_BUILD_IDS = PERSON_A_DIR / "split_build_ids.txt"
DEFAULT_EVAL_IDS = PERSON_A_DIR / "split_eval_ids.txt"
DEFAULT_EXCLUDED = PERSON_A_DIR / "excluded_cases.csv"
DEFAULT_MANIFEST = PERSON_A_DIR / "dataset_manifest.json"
DEFAULT_OUTPUT = HANDOFF_DIR_V11
DEFAULT_ALIAS_MAP = CLEANING_DIR / "实体别名映射.json"
DEFAULT_CLEANING_SUMMARY = CLEANING_DIR / "name_cleaning_summary.json"

CASE_ID_RE = re.compile(r"^case_\d{6}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_ids(path: Path) -> list[str]:
    ids = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"{path} 存在重复 ID")
    bad = [value for value in ids if not CASE_ID_RE.fullmatch(value)]
    if bad:
        raise RuntimeError(f"{path} 存在非法 ID，例如 {bad[:3]}")
    return ids


def read_excluded(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_cases_json(cases_path: Path, target: Path) -> tuple[dict[str, str], int]:
    """把 cases_master.jsonl 转成 KEO 的案例数据.json，返回 id->类别 和案例数。"""
    case_categories: dict[str, str] = {}
    count = 0
    with cases_path.open("r", encoding="utf-8") as source, target.open(
        "w", encoding="utf-8", newline="\n"
    ) as output:
        output.write("[\n")
        first = True
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            case_id = record.get("source_id")
            text = record.get("text")
            category = record.get("category")
            if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
                raise RuntimeError(f"非法 source_id: {case_id!r}")
            if case_id in case_categories:
                raise RuntimeError(f"重复案例 ID: {case_id}")
            if not isinstance(text, str) or not text:
                raise RuntimeError(f"{case_id} 的 text 为空")
            if not isinstance(category, str) or not category:
                raise RuntimeError(f"{case_id} 的 category 为空")
            row = {"id": case_id, "案情描述": text, "案件类别": category}
            if not first:
                output.write(",\n")
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            first = False
            case_categories[case_id] = category
            count += 1
        output.write("\n]\n")
    if count == 0:
        raise RuntimeError("案例数据为空")
    return case_categories, count


def write_split(build_ids: list[str], eval_ids: list[str], target: Path) -> None:
    build_set = set(build_ids)
    eval_set = set(eval_ids)
    overlap = build_set & eval_set
    if overlap:
        raise RuntimeError(f"建库/评估名单有交集，例如 {sorted(overlap)[:5]}")
    payload = {"build_ids": sorted(build_set), "eval_ids": sorted(eval_set)}
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_triples(
    triples_path: Path,
    case_categories: dict[str, str],
    build_ids: set[str],
    eval_ids: set[str],
    excluded_ids: set[str],
) -> dict[str, int]:
    total = build_triples = eval_triples = excluded_triples = 0
    source_missing = category_mismatch = duplicate_triple_id = 0
    seen_triple_ids: set[str] = set()
    with triples_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            total += 1
            source_id = record.get("source_id")
            if source_id not in case_categories:
                source_missing += 1
            elif record.get("fraud_category") != case_categories[source_id]:
                category_mismatch += 1
            if source_id in build_ids:
                build_triples += 1
            elif source_id in eval_ids:
                eval_triples += 1
            elif source_id in excluded_ids:
                excluded_triples += 1
            triple_id = record.get("triple_id")
            if triple_id in seen_triple_ids:
                duplicate_triple_id += 1
            else:
                seen_triple_ids.add(triple_id)
    if source_missing:
        raise RuntimeError(f"{source_missing} 条三元组的 source_id 不在案例数据中")
    if category_mismatch:
        raise RuntimeError(f"{category_mismatch} 条三元组的类别与案例不一致")
    if duplicate_triple_id:
        raise RuntimeError(f"{duplicate_triple_id} 条三元组 triple_id 重复")
    if excluded_triples:
        raise RuntimeError(f"{excluded_triples} 条三元组来自排除案例")
    if build_triples + eval_triples != total:
        raise RuntimeError("建库/评估三元组数量之和与总数不一致")
    return {
        "triples": total,
        "build_triples": build_triples,
        "eval_triples": eval_triples,
        "unique_triple_ids": len(seen_triple_ids),
        "source_missing": source_missing,
        "category_mismatch": category_mismatch,
        "duplicate_triple_id": duplicate_triple_id,
        "excluded_triples": excluded_triples,
    }


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def build_delivery_note(
    version: str,
    generated_at: str,
    files: list[tuple[str, Path, str]],
    counts: dict[str, int],
    triple_stats: dict[str, int],
    excluded_rows: list[dict[str, str]],
    quality_rows: list[dict[str, str]],
    source_paths: dict[str, Path],
    output_dir: Path,
    status: str = "draft_pending_freeze",
    cleaning: dict | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# 人员 A KEO 格式 1.1 交付说明")
    lines.append("")
    lines.append(f"> 包版本：`{version}`  ")
    lines.append(f"> 生成时间：{generated_at}  ")
    lines.append(f"> 状态：**{status}**  ")
    if cleaning:
        lines.append("> 名称清洗：**已完成（P0-5，v{}）**  ".format(cleaning.get("version", "")))
    else:
        lines.append("> 名称清洗：**未完成（P0-5）**  ")
    lines.append("")
    lines.append("## 1. 包内容")
    lines.append("")
    lines.append("| 文件 | 大小 | SHA256 |")
    lines.append("|---|---:|---|")
    for name, path, digest in files:
        lines.append(f"| `{name}` | {format_size(path.stat().st_size)} | `{digest}` |")
    lines.append("")
    lines.append("## 2. 数量")
    lines.append("")
    lines.append("| 项目 | 数量 |")
    lines.append("|---|---:|")
    lines.append(f"| 案例数据 | {counts['cases']:,} |")
    lines.append(f"| 三元组数据 | {counts['triples']:,} |")
    lines.append(f"| 建库案例 `build_ids` | {counts['build']:,} |")
    lines.append(f"| 评估案例 `eval_ids` | {counts['eval']:,} |")
    lines.append(f"| 建库三元组 | {triple_stats['build_triples']:,} |")
    lines.append(f"| 评估三元组 | {triple_stats['eval_triples']:,} |")
    lines.append(f"| 排除案例 | {counts['excluded']:,} |")
    lines.append("")
    lines.append("`案例数据.json` 包含全部 {cases:,} 条案例，保留原始编号；`build_ids` 和 `eval_ids` 只覆盖 {usable:,} 条可用案例。{excluded} 条排除案例不在任何一份名单中。".format(cases=counts["cases"], usable=counts["build"] + counts["eval"], excluded=counts["excluded"]))
    lines.append("")
    lines.append("`三元组数据.jsonl` 包含全部三元组，C 应按 `build_ids` 过滤后建图，D 不应把建库三元组用于评估。")
    lines.append("")
    lines.append("## 3. 使用方式")
    lines.append("")
    lines.append("- **B**：从 `案例数据.json` 中筛选 `id in build_ids` 的案例，建立文本检索。")
    lines.append("- **C**：从 `三元组数据.jsonl` 中筛选 `source_id in build_ids` 的三元组，并与 `案例数据.json` 关联建图。")
    lines.append("- **D**：从 `案例数据.json` 中筛选 `id in eval_ids` 的案例，用于出题和评估。")
    lines.append("")
    lines.append("关联键：案例 `id` = 三元组 `source_id`。")
    lines.append("")
    lines.append("## 4. 排除案例")
    lines.append("")
    failed_count = sum(1 for row in excluded_rows if row.get("reason") == "extraction_failed")
    zero_count = sum(1 for row in excluded_rows if row.get("reason") == "zero_triples")
    lines.append(
        f"共 {counts['excluded']} 条，不进入建库和评估：{failed_count} 条抽取失败，{zero_count} 条成功但 0 三元组。"
    )
    lines.append("")
    lines.append("| source_id | 类别 | 分区 | 原因 | 原文长度 |")
    lines.append("|---|---|---|---|---:|")
    for row in excluded_rows:
        lines.append(
            "| {source_id} | {category} | {partition} | {reason} | {text_length} |".format(
                source_id=row.get("source_id", ""),
                category=row.get("category", ""),
                partition=row.get("partition", ""),
                reason=row.get("reason", ""),
                text_length=row.get("text_length", ""),
            )
        )
    lines.append("")
    lines.append("47 条原文 ≥50 字的零三元组案例属于疑似抽取遗漏，当前作为已接受风险，不补跑。")
    lines.append("")
    lines.append("## 5. 质量标记")
    lines.append("")
    lines.append("| 标记 | 三元组数 | 案例数 |")
    lines.append("|---|---:|---:|")
    for row in quality_rows:
        lines.append(
            "| {issue_type} | {triple_count} | {case_count} |".format(
                issue_type=row.get("issue_type", ""),
                triple_count=row.get("triple_count", "") or "0",
                case_count=row.get("case_count", "") or "",
            )
        )
    lines.append("")
    lines.append("质量标记只表示问题，不表示已经删除。C 建图时应按 `C图谱RAG建图规则说明.md` 处理，不要静默丢弃原始三元组。")
    lines.append("")
    lines.append("## 6. 名称清洗状态")
    lines.append("")
    if cleaning:
        rules = cleaning.get("by_rule", {})
        review = cleaning.get("review") or {}
        lines.append(
            "`head.name` / `tail.name` 已完成清洗（v{}），规则与计数如下：".format(cleaning.get("version", ""))
        )
        lines.append("")
        lines.append("| 规则 | 影响提及 |")
        lines.append("|---|---:|")
        lines.append(f"| A 去首尾空白 | {rules.get('A_whitespace', 0):,} |")
        lines.append(f"| B2 空名称 → 未知 | {rules.get('B_empty_to_unknown', 0):,} |")
        lines.append(f"| C1 UNKNOWN → 未知 | {rules.get('C_unknown_to_unknown_cn', 0):,} |")
        lines.append(f"| D 自动别名合并 | {rules.get('D_auto_alias', 0):,} |")
        lines.append(f"| E review 官方化合并 | {rules.get('E_review_alias', 0):,} |")
        lines.append("")
        lines.append(
            "采用自动别名的组：{groups:,} 组；auto 映射 `(类型, 名称)` 对：{pairs:,} 个；".format(
                groups=cleaning.get("auto_groups", 0),
                pairs=cleaning.get("alias_mapping_pairs", 0),
            )
            + "review 官方化映射：{review:,} 对；冲突名称：{conflicts:,} 个。".format(
                review=cleaning.get("review_merge_pairs", 0),
                conflicts=cleaning.get("conflict_names", 0),
            )
        )
        lines.append("")
        lines.append("清洗口径（已确认）：")
        lines.append("")
        lines.append("- 归一只用于比较，显示名保留组内最高频的原始写法；")
        lines.append("- 空名称与 `UNKNOWN` 统一写为“未知”，原值保留在 `head.original_name` / `tail.original_name`；")
        lines.append("- 自动别名只合并 `recommended_action=auto` 的组；`hold` 组不合并；")
        lines.append("- 机构规范名：带“中国”写法为多数票时采用；")
        lines.append("- 只改 `name`，不改 `head.id` / `tail.id`；人员不跨案合并；不同类型不合并。")
        if cleaning.get("review_merge_pairs"):
            lines.append(
                "- review 组按官方来源核验后合并（腾讯应用宝 + 必应国内版），"
                f"本次应用 {cleaning['review_merge_pairs']:,} 个 `(类型, 名称)` 对；"
                "`々·々`、`+` 等纯噪声按确认口径保留。"
            )
        lines.append("")
        lines.append(
            "`实体别名映射.json` 是 C 建图时可直接读取的正式映射表"
            "（含 auto 组、review 官方化映射 `review_mappings` 与冲突解析记录）。"
        )
    else:
        lines.append("当前 `head.name` / `tail.name` 仍是抽取阶段名称，**尚未完成 P0-5 名称清洗和别名映射**。")
        lines.append("")
        lines.append("本包为 `1.0-draft`，可用于 B/C/D 联调和结构校验；**不建议 C 直接用于正式建图**。")
    lines.append("")
    lines.append("## 7. 输入来源")
    lines.append("")
    lines.append("| 用途 | 路径 |")
    lines.append("|---|---|")
    for label, path in source_paths.items():
        shown = rel(path) if isinstance(path, Path) and path.is_file() else path
        lines.append(f"| {label} | `{shown}` |")
    lines.append("")
    lines.append("## 8. 复现命令")
    lines.append("")
    lines.append("```powershell")
    lines.append("python -m experiments.KEO.core.data.export_keo \\")
    lines.append(f"  --output \"{rel(output_dir)}\" \\")
    lines.append(f"  --version \"{version}\"")
    lines.append("```")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出 KEO 格式 1.1 的人员 A 正式交付包")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="cases_master.jsonl")
    parser.add_argument(
        "--triples",
        default=str(DEFAULT_TRIPLES),
        help="三元组主表；存在清洗产物时默认用 name_cleaning/apply_v1.0/triples_clean_v2.jsonl",
    )
    parser.add_argument("--build-ids", default=str(DEFAULT_BUILD_IDS), help="split_build_ids.txt")
    parser.add_argument("--eval-ids", default=str(DEFAULT_EVAL_IDS), help="split_eval_ids.txt")
    parser.add_argument("--excluded", default=str(DEFAULT_EXCLUDED), help="excluded_cases.csv")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="dataset_manifest.json")
    parser.add_argument("--issues", default=str(DEFAULT_PERSON_A / "issues_summary.csv"), help="issues_summary.csv")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出目录")
    parser.add_argument("--version", default="1.0-draft", help="包版本")
    parser.add_argument("--status", default="draft_pending_freeze", help="包状态（写入交付说明）")
    parser.add_argument("--alias-map", default=str(DEFAULT_ALIAS_MAP), help="实体别名映射.json（存在则并入交付包）")
    parser.add_argument("--cleaning-summary", default=str(DEFAULT_CLEANING_SUMMARY), help="名称清洗汇总 JSON")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出目录")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    cases_path = Path(args.cases)
    triples_path = Path(args.triples)
    build_path = Path(args.build_ids)
    eval_path = Path(args.eval_ids)
    excluded_path = Path(args.excluded)
    manifest_path = Path(args.manifest)
    issues_path = Path(args.issues)
    alias_map_path = Path(args.alias_map).expanduser() if args.alias_map else None
    cleaning_summary_path = (
        Path(args.cleaning_summary).expanduser() if args.cleaning_summary else None
    )
    output_dir = Path(args.output).expanduser()

    for path in (cases_path, triples_path, build_path, eval_path, excluded_path, manifest_path, issues_path):
        if not path.is_file():
            raise SystemExit(f"输入不存在: {path}")

    cleaning: dict | None = None
    if cleaning_summary_path is not None and cleaning_summary_path.is_file():
        cleaning = json.loads(cleaning_summary_path.read_text(encoding="utf-8"))
        if alias_map_path is None or not alias_map_path.is_file():
            raise SystemExit(f"已提供清洗汇总但缺少别名映射文件: {alias_map_path}")
        verification = cleaning.get("verification") or {}
        if verification.get("triples") != 1008822 or verification.get("triples_match_expected") is False:
            raise SystemExit(f"清洗汇总校验异常: {verification}")
    elif alias_map_path is not None and alias_map_path.is_file():
        raise SystemExit(f"提供了别名映射但缺少清洗汇总: {cleaning_summary_path}")

    build_ids = read_ids(build_path)
    eval_ids = read_ids(eval_path)
    build_set = set(build_ids)
    eval_set = set(eval_ids)
    overlap = build_set & eval_set
    if overlap:
        raise SystemExit(f"建库/评估名单有交集，例如 {sorted(overlap)[:5]}")
    if len(build_set) != 49289 or len(eval_set) != 32859:
        raise SystemExit(
            f"名单数量异常: build={len(build_set)}, eval={len(eval_set)}；预期 49,289 / 32,859"
        )

    excluded_rows = read_excluded(excluded_path)
    excluded_ids = {row["source_id"] for row in excluded_rows}
    if len(excluded_ids) != 62:
        raise SystemExit(f"排除案例数量异常: {len(excluded_ids)}；预期 62")
    if (build_set | eval_set) & excluded_ids:
        raise SystemExit("排除案例出现在建库/评估名单中")

    with issues_path.open("r", encoding="utf-8-sig", newline="") as file:
        quality_rows = list(csv.DictReader(file))

    if output_dir.exists():
        if not args.overwrite:
            raise SystemExit(f"输出目录已存在: {output_dir}；如需覆盖请加 --overwrite")
        shutil.rmtree(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = output_dir.with_name(output_dir.name + ".tmp")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        case_categories, case_count = write_cases_json(cases_path, temp_dir / "案例数据.json")
        if set(case_categories) != set(build_set) | set(eval_set) | excluded_ids:
            raise RuntimeError("案例数据、划分名单和排除名单没有覆盖同一批案例")

        shutil.copyfile(triples_path, temp_dir / "三元组数据.jsonl")
        write_split(build_ids, eval_ids, temp_dir / "划分名单.json")
        if alias_map_path is not None and alias_map_path.is_file():
            shutil.copyfile(alias_map_path, temp_dir / "实体别名映射.json")

        triple_stats = verify_triples(
            temp_dir / "三元组数据.jsonl",
            case_categories,
            build_set,
            eval_set,
            excluded_ids,
        )

        files: list[tuple[str, Path, str]] = []
        package_names = ["案例数据.json", "三元组数据.jsonl", "划分名单.json"]
        if (temp_dir / "实体别名映射.json").is_file():
            package_names.append("实体别名映射.json")
        for name in package_names:
            path = temp_dir / name
            files.append((name, path, sha256_file(path)))

        counts = {
            "cases": case_count,
            "triples": triple_stats["triples"],
            "build": len(build_set),
            "eval": len(eval_set),
            "excluded": len(excluded_ids),
        }
        source_paths = {
            "案例主表": cases_path,
            "三元组主表": triples_path,
            "建库名单": build_path,
            "评估名单": eval_path,
            "排除案例": excluded_path,
            "数据清单": manifest_path,
            "问题汇总": issues_path,
            "别名映射": alias_map_path if alias_map_path is not None else "",
            "清洗汇总": cleaning_summary_path if cleaning_summary_path is not None else "",
        }
        note = build_delivery_note(
            args.version,
            generated_at,
            files,
            counts,
            triple_stats,
            excluded_rows,
            quality_rows,
            source_paths,
            output_dir,
            status=args.status,
            cleaning=cleaning,
        )
        (temp_dir / "交付说明.md").write_text(note, encoding="utf-8")
        temp_dir.rename(output_dir)
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise

    print(
        json.dumps(
            {
                "output": str(output_dir),
                "version": args.version,
                "generated_at": generated_at,
                "counts": counts,
                "triple_stats": triple_stats,
                "files": {
                    name: {
                        "size_bytes": (output_dir / name).stat().st_size,
                        "sha256": digest,
                    }
                    for name, path, digest in files
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
