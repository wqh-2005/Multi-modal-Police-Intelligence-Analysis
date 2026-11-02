from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


from .paths import CLEANING_DIR, NAME_CLEANING_DIR, PERSON_A_DIR, REVIEW_RESOLUTION_DIR

DEFAULT_PERSON_A = PERSON_A_DIR
DEFAULT_TRIPLES = PERSON_A_DIR / "triples_clean.jsonl"
DEFAULT_CANDIDATES = NAME_CLEANING_DIR / "entity_alias_candidates.json"
DEFAULT_REVIEW_MERGE = REVIEW_RESOLUTION_DIR / "review_alias_merge.json"
DEFAULT_OUTPUT = CLEANING_DIR

CLEAN_VERSION = "1.1"
PLACEHOLDER_CANONICAL = "未知"
UNKNOWN_KEYS = {"unknown"}

RULE_B_EMPTY = "B2_empty_to_unknown"
RULE_C_UNKNOWN = "C1_unknown_to_unknown_cn"
RULE_D_ALIAS = "D_auto_alias"
RULE_E_REVIEW = "E_review_alias"
RULE_A_WHITESPACE = "A_whitespace_display"


def normalize_key(name: str) -> str:
    """规则 A：仅用于比较/合并的归一化键（NFKC + 去空白 + 折叠空白 + casefold）。"""
    value = unicodedata.normalize("NFKC", name)
    value = value.replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value.casefold()


def prefer_china_prefix(group: dict[str, Any]) -> dict[str, Any]:
    """机构规范名：仅当“带中国写法”是多数票时，才用它做规范名。"""
    if group.get("entity_type") != "机构":
        return group
    prefixed = [member for member in group["members"] if member["name"].startswith("中国")]
    plain = [member for member in group["members"] if not member["name"].startswith("中国")]
    if not prefixed:
        return group
    prefixed_mentions = sum(member["mentions"] for member in prefixed)
    plain_mentions = sum(member["mentions"] for member in plain)
    if prefixed_mentions < plain_mentions:
        return group
    best = sorted(prefixed, key=lambda member: (-member["mentions"], member["name"]))[0]["name"]
    if best == group["canonical_candidate"]:
        return group
    updated = dict(group)
    updated["canonical_candidate"] = best
    updated["canonical_rule"] = "prefer_china_prefix"
    return updated


def load_auto_alias_map(
    candidates_path: Path,
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    groups = payload["groups"]
    auto_groups = [
        prefer_china_prefix(group) for group in groups if group.get("recommended_action") == "auto"
    ]

    # (entity_type, member_name) -> 候选规范名列表
    proposals: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for group in auto_groups:
        canonical = group["canonical_candidate"]
        for member in group["members"]:
            name = member["name"]
            if not isinstance(name, str) or not name or name == canonical:
                continue
            proposals[(group["entity_type"], name)].append(
                {
                    "canonical": canonical,
                    "priority": int(group.get("apply_priority") or 0),
                    "member_mentions": int(group.get("member_mentions") or 0),
                    "group_id": group.get("group_id", ""),
                    "reason": group.get("reason", ""),
                }
            )

    alias_map: dict[tuple[str, str], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for key, items in proposals.items():
        ordered = sorted(
            items,
            key=lambda item: (-item["priority"], -item["member_mentions"], item["group_id"]),
        )
        winner = ordered[0]
        alias_map[key] = winner
        distinct = {item["canonical"] for item in items}
        if len(distinct) > 1:
            conflicts.append(
                {
                    "entity_type": key[0],
                    "name": key[1],
                    "chosen": winner["canonical"],
                    "candidates": sorted(distinct),
                    "resolved_by": "apply_priority>member_mentions>group_id",
                }
            )
    return alias_map, auto_groups, sorted(conflicts, key=lambda item: (item["entity_type"], item["name"]))


def load_review_merge(path: Path | None) -> dict[tuple[str, str], str]:
    if path is None or not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {(item["entity_type"], item["original"]): item["canonical"] for item in payload.get("mappings", [])}


def resolve_chain(
    entity_type: str,
    name: str,
    alias_map: dict[tuple[str, str], dict[str, Any]],
    review_map: dict[tuple[str, str], str],
    depth: int = 4,
) -> tuple[str, str | None, dict[str, Any] | None]:
    """沿自动别名与 review 别名链式收敛，返回最终名称与首个命中的规则。"""
    current = name
    first_rule: str | None = None
    hit_info: dict[str, Any] | None = None
    seen: set[tuple[str, str]] = set()
    for _ in range(depth):
        key = (entity_type, current)
        if key in seen:
            break
        seen.add(key)
        auto = alias_map.get(key)
        review = review_map.get(key)
        if review is not None and review != current:
            first_rule = first_rule or RULE_E_REVIEW
            current = review
            continue
        if auto is not None and auto["canonical"] != current:
            first_rule = first_rule or RULE_D_ALIAS
            hit_info = hit_info or auto
            current = auto["canonical"]
            continue
        break
    return current, first_rule, hit_info


def clean_name(
    name: str,
    entity_type: str,
    alias_map: dict[tuple[str, str], dict[str, Any]],
    review_map: dict[tuple[str, str], str] | None = None,
) -> tuple[str, str | None, dict[str, Any] | None]:
    """返回 (新名称, 命中规则, 别名命中信息)。"""
    if not isinstance(name, str):
        raise TypeError(f"name 不是字符串: {name!r}")
    if name == "":
        return PLACEHOLDER_CANONICAL, RULE_B_EMPTY, None
    if normalize_key(name) in UNKNOWN_KEYS:
        return PLACEHOLDER_CANONICAL, RULE_C_UNKNOWN, None
    resolved, rule, hit = resolve_chain(entity_type, name, alias_map, review_map or {})
    if rule is not None:
        return resolved, rule, hit
    stripped = name.strip()
    if stripped and stripped != name:
        return stripped, RULE_A_WHITESPACE, None
    return name, None, None


def clean_mention(
    mention: dict[str, Any],
    alias_map: dict[tuple[str, str], dict[str, Any]],
    stats: "CleanStats",
    review_map: dict[tuple[str, str], str] | None = None,
) -> None:
    if not isinstance(mention, dict):
        return
    name = mention.get("name")
    entity_type = mention.get("type")
    if name is None:
        return
    new_name, rule, hit = clean_name(name, entity_type, alias_map, review_map)
    stats.mentions += 1
    if rule is None:
        return
    stats.by_rule[rule] += 1
    stats.by_type_rule[(entity_type, rule)] += 1
    mention["original_name"] = name
    mention["name"] = new_name
    stats.pair_counter[(entity_type, name, new_name, rule)] += 1
    if rule == RULE_D_ALIAS and hit is not None:
        stats.alias_hits[hit["group_id"]] += 1


class CleanStats:
    def __init__(self) -> None:
        self.triples = 0
        self.mentions = 0
        self.by_rule: Counter[str] = Counter()
        self.by_type_rule: Counter[tuple[str, str]] = Counter()
        self.pair_counter: Counter[tuple[str, str, str, str]] = Counter()
        self.alias_hits: Counter[str] = Counter()
        self.seen_triple_ids: set[str] = set()
        self.duplicate_triple_ids = 0


def clean_file(
    source: Path,
    target: Path,
    alias_map,
    stats: CleanStats,
    limit: int | None = None,
    review_map: dict[tuple[str, str], str] | None = None,
) -> None:
    with source.open("r", encoding="utf-8") as reader, target.open("w", encoding="utf-8", newline="\n") as writer:
        for line in reader:
            if not line.strip():
                continue
            record = json.loads(line)
            stats.triples += 1
            triple_id = record.get("triple_id")
            if isinstance(triple_id, str):
                if triple_id in stats.seen_triple_ids:
                    stats.duplicate_triple_ids += 1
                else:
                    stats.seen_triple_ids.add(triple_id)
            clean_mention(record.get("head"), alias_map, stats, review_map)
            clean_mention(record.get("tail"), alias_map, stats, review_map)
            writer.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            if limit is not None and stats.triples >= limit:
                break


def write_alias_file(
    target: Path,
    alias_map: dict[tuple[str, str], dict[str, Any]],
    auto_groups: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    stats: CleanStats,
    generated_at: str,
    review_map: dict[tuple[str, str], str] | None = None,
) -> None:
    mappings: list[dict[str, Any]] = []
    for (entity_type, name), hit in sorted(alias_map.items(), key=lambda item: (item[0][0], item[0][1])):
        mappings.append(
            {
                "entity_type": entity_type,
                "original": name,
                "canonical": hit["canonical"],
                "group_id": hit["group_id"],
                "reason": hit["reason"],
                "apply_priority": hit["priority"],
                "mentions": stats.pair_counter.get((entity_type, name, hit["canonical"], RULE_D_ALIAS), 0),
            }
        )
    groups_payload = [
        {
            "group_id": group["group_id"],
            "entity_type": group["entity_type"],
            "canonical": group["canonical_candidate"],
            "members": group["members"],
            "reason": group["reason"],
            "apply_priority": group.get("apply_priority"),
            "canonical_rule": group.get("canonical_rule", "most_frequent_member"),
        }
        for group in auto_groups
    ]
    payload = {
        "version": CLEAN_VERSION,
        "generated_at": generated_at,
        "status": "confirmed_by_project_group",
        "applies_to": "KEO 格式 1.1 三元组数据.jsonl 的 head/tail.name",
        "decisions": {
            "rule_a": "归一只用于比较；显示名保留组内最高频的原始写法（NFKC/大小写不改显示）",
            "rule_b": "空名称统一写为“未知”，原值保存在 original_name",
            "rule_c": "UNKNOWN / unknown 统一写为“未知”",
            "rule_d": "只应用 recommended_action=auto 的组；review/hold 组不合并",
            "rule_d_canonical": "机构规范名：带“中国”写法的提及数 ≥ 无前缀写法时采用带前缀写法，否则取组内最高频原写法",
            "scope": "只改 name，不改 head.id / tail.id；人员不跨案合并；不同 type 不合并",
        },
        "normalize_rule": "NFKC + 去首尾空白 + 连续空白折叠 + casefold（仅比较用）",
        "mapping_count": len(mappings),
        "review_mapping_count": len(review_map or {}),
        "review_mappings": [
            {
                "entity_type": key[0],
                "original": key[1],
                "canonical": canonical,
                "rule": RULE_E_REVIEW,
                "mentions": stats.pair_counter.get((key[0], key[1], canonical, RULE_E_REVIEW), 0),
            }
            for key, canonical in sorted((review_map or {}).items())
        ],
        "group_count": len(groups_payload),
        "china_prefix_groups": sum(1 for group in auto_groups if group.get("canonical_rule") == "prefer_china_prefix"),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "mappings": mappings,
        "groups": groups_payload,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_changes_csv(target: Path, stats: CleanStats) -> None:
    with gzip.open(target, "wt", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["entity_type", "original_name", "cleaned_name", "rule", "mentions"])
        for (entity_type, original, cleaned, rule), mentions in sorted(
            stats.pair_counter.items(), key=lambda item: (-item[1], item[0])
        ):
            writer.writerow([entity_type, original, cleaned, rule, mentions])


def verify_output(
    path: Path,
    alias_map: dict[tuple[str, str], dict[str, Any]],
    expected_triples: int,
    review_map: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """第二遍校验：数量、ID 唯一、名称合规、映射一致。"""
    triples = 0
    mentions = 0
    empty_names = 0
    unknown_left = 0
    whitespace_left = 0
    alias_left = 0
    inconsistent = 0
    seen: set[str] = set()
    duplicates = 0
    examples: dict[str, list[str]] = defaultdict(list)
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            triples += 1
            triple_id = record.get("triple_id")
            if isinstance(triple_id, str):
                if triple_id in seen:
                    duplicates += 1
                else:
                    seen.add(triple_id)
            for slot in ("head", "tail"):
                mention = record.get(slot)
                if not isinstance(mention, dict):
                    continue
                name = mention.get("name")
                entity_type = mention.get("type")
                if name is None:
                    continue
                mentions += 1
                if name == "":
                    empty_names += 1
                    if len(examples["empty"]) < 5:
                        examples["empty"].append(f"{triple_id}:{slot}")
                    continue
                if normalize_key(name) in UNKNOWN_KEYS:
                    unknown_left += 1
                    if len(examples["unknown"]) < 5:
                        examples["unknown"].append(f"{triple_id}:{slot}:{name}")
                if name != name.strip():
                    whitespace_left += 1
                    if len(examples["whitespace"]) < 5:
                        examples["whitespace"].append(f"{triple_id}:{slot}:{name!r}")
                hit = alias_map.get((entity_type, name))
                review_hit = (review_map or {}).get((entity_type, name))
                if hit is not None or review_hit is not None:
                    alias_left += 1
                    if len(examples["alias"]) < 5:
                        examples["alias"].append(f"{triple_id}:{slot}:{entity_type}/{name}")
                original = mention.get("original_name")
                if original is not None:
                    expected, rule, _ = clean_name(original, entity_type, alias_map, review_map)
                    if expected != name:
                        inconsistent += 1
                        if len(examples["inconsistent"]) < 5:
                            examples["inconsistent"].append(f"{triple_id}:{slot}:{original!r}->{name!r}")
    return {
        "triples": triples,
        "triples_match_expected": triples == expected_triples,
        "unique_triple_ids": len(seen),
        "duplicate_triple_ids": duplicates,
        "mentions": mentions,
        "empty_names_left": empty_names,
        "unknown_left": unknown_left,
        "whitespace_left": whitespace_left,
        "alias_left": alias_left,
        "original_name_inconsistent": inconsistent,
        "examples": dict(examples),
    }


def build_report(
    summary: dict[str, Any],
    stats: CleanStats,
    verification: dict[str, Any],
    conflicts: list[dict[str, Any]],
    auto_groups: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# 人员 A 实体名称清洗执行报告（P0-5）")
    lines.append("")
    lines.append(f"> 版本：`{summary['version']}`  ")
    lines.append(f"> 生成时间：{summary['generated_at']}  ")
    lines.append(f"> 输入：`{summary['source_triples']}`  ")
    lines.append(f"> 输出：`{summary['output_triples']}`  ")
    lines.append("> 状态：**已执行，待冻结**")
    lines.append("")
    lines.append("## 1. 已确认口径")
    lines.append("")
    lines.append("| 规则 | 确认结果 | 影响 |")
    lines.append("|---|---|---|")
    lines.append("| A 确定性归一化 | 归一只用于比较；显示名保留组内最高频原始写法 | NFKC/大小写不改显示名，仅首尾空白会被去掉 |")
    lines.append("| B 空名称 | B2：统一写“未知” | 原值保留在 `original_name` |")
    lines.append("| C UNKNOWN | C1：`UNKNOWN`/`unknown` 统一写“未知” | 占位名收敛为一种 |")
    lines.append("| D 别名合并 | 只应用 `recommended_action=auto` 的组 | `hold` 组不合并 |")
    lines.append("| E review 官方化 | 1,432 组 review 经应用宝/必应官方核验后合并，2 组纯噪声保留 | 口径见 `review_resolution/` |")
    lines.append("| D 机构规范名 | “带中国写法”为多数票时采用它 | 否则取组内最高频原写法 |")
    lines.append("")
    lines.append("## 2. 执行范围")
    lines.append("")
    lines.append(f"- 三元组：{stats.triples:,} 条")
    lines.append(f"- 实体提及（head + tail）：{stats.mentions:,} 次")
    lines.append(f"- 采用自动别名的组：{len(auto_groups):,} 组（其中 {sum(1 for g in auto_groups if g.get('canonical_rule') == 'prefer_china_prefix'):,} 组因“中国”前缀改写规范名）")
    lines.append(f"- 实际映射的 `(类型, 原始名称)` 对：{len(stats.pair_counter):,} 个变化项")
    lines.append("")
    lines.append("## 3. 变更统计")
    lines.append("")
    lines.append("| 规则 | 影响提及 |")
    lines.append("|---|---:|")
    labels = {
        RULE_A_WHITESPACE: "A 去首尾空白",
        RULE_B_EMPTY: "B2 空名称 → 未知",
        RULE_C_UNKNOWN: "C1 UNKNOWN → 未知",
        RULE_D_ALIAS: "D 自动别名合并",
        RULE_E_REVIEW: "E review 官方化合并",
    }
    total_changed = 0
    for rule, label in labels.items():
        count = stats.by_rule.get(rule, 0)
        total_changed += count
        lines.append(f"| {label} | {count:,} |")
    lines.append(f"| **合计（去重后）** | **{total_changed:,}** |")
    lines.append("")
    lines.append("按实体类型拆分：")
    lines.append("")
    lines.append("| 实体类型 | A 空白 | B2 空名 | C1 UNKNOWN | D 别名 | E review |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    types = sorted({entity_type for entity_type, _ in stats.by_type_rule})
    for entity_type in types:
        lines.append(
            "| {} | {:,} | {:,} | {:,} | {:,} | {:,} |".format(
                entity_type,
                stats.by_type_rule.get((entity_type, RULE_A_WHITESPACE), 0),
                stats.by_type_rule.get((entity_type, RULE_B_EMPTY), 0),
                stats.by_type_rule.get((entity_type, RULE_C_UNKNOWN), 0),
                stats.by_type_rule.get((entity_type, RULE_D_ALIAS), 0),
                stats.by_type_rule.get((entity_type, RULE_E_REVIEW), 0),
            )
        )
    lines.append("")
    lines.append("## 4. 冲突处理")
    lines.append("")
    lines.append(
        f"同一名称同时落入多个 auto 组时按 `apply_priority`（contain=2 > exact=1）→ `member_mentions` → `group_id` 排序取唯一规范名，共处理 {len(conflicts):,} 个冲突名称。"
    )
    if conflicts:
        lines.append("")
        lines.append("| 实体类型 | 名称 | 采用规范名 | 其他候选 |")
        lines.append("|---|---|---|---|")
        for item in conflicts[:20]:
            others = [value for value in item["candidates"] if value != item["chosen"]]
            lines.append(
                "| {} | {} | {} | {} |".format(
                    item["entity_type"], item["name"], item["chosen"], "、".join(others) or "-"
                )
            )
        if len(conflicts) > 20:
            lines.append(f"| … | 其余 {len(conflicts) - 20:,} 项见 `实体别名映射.json` | | |")
    lines.append("")
    lines.append("## 5. 未合并项（按确认口径保留）")
    lines.append("")
    lines.append(
        f"- `review` 组 1,434 组：已官方化合并 {summary.get('review_merge_pairs', 0):,} 个 `(类型, 名称)` 对，"
        "仅 `々·々`、`+` 两类纯噪声按确认口径保留；"
    )
    lines.append("- review 核验来源：腾讯应用宝（sj.qq.com）+ 必应国内版（cn.bing.com），逐组记录在 `review_resolution/review_alias_decisions.json`；")
    lines.append("- `hold` 组 898 组：泛化名称、占位名称、后缀差异过大的名称，一律不合并；")
    lines.append("- 人员：不跨案合并，同名保留为案件内不同节点；")
    lines.append("- 身份角色：不做包含关系自动合并（客服 ≠ 京东客服）；")
    lines.append("- 不同实体类型之间不合并。")
    lines.append("")
    lines.append("## 6. 校验结果")
    lines.append("")
    lines.append("| 校验项 | 结果 |")
    lines.append("|---|---|")
    lines.append(f"| 三元组总数 | {verification['triples']:,}（预期一致：{verification['triples_match_expected']}） |")
    lines.append(f"| `triple_id` 唯一数 | {verification['unique_triple_ids']:,}（重复 {verification['duplicate_triple_ids']}） |")
    lines.append(f"| 剩余空名称 | {verification['empty_names_left']} |")
    lines.append(f"| 剩余 `UNKNOWN` 占位 | {verification['unknown_left']} |")
    lines.append(f"| 剩余首尾空白名称 | {verification['whitespace_left']} |")
    lines.append(f"| 可继续合并的 auto 别名残留 | {verification['alias_left']} |")
    lines.append(f"| `original_name` 与清洗结果不一致 | {verification['original_name_inconsistent']} |")
    lines.append("")
    lines.append("## 7. 输出文件")
    lines.append("")
    lines.append("- `triples_clean_v2.jsonl`：清洗后三元组，`head`/`tail` 被改名时带 `original_name`；")
    lines.append("- `实体别名映射.json`：C 可直接读取的别名映射（含 auto 组、review 官方化映射、冲突记录）；")
    lines.append("- `name_cleaning_changes.csv.gz`：全部 `(类型, 原名 → 新名, 规则, 提及数)`；")
    lines.append("- `name_cleaning_summary.json`：机器可读汇总；")
    lines.append("- `name_cleaning_report.md`：本文件。")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行人员 A 实体名称清洗（P0-5）")
    parser.add_argument("--triples", default=str(DEFAULT_TRIPLES), help="清洗前三元组")
    parser.add_argument("--candidates", default=str(DEFAULT_CANDIDATES), help="别名候选 JSON")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT), help="输出目录")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条（调试用）")
    parser.add_argument(
        "--review-merge",
        default=str(DEFAULT_REVIEW_MERGE),
        help="review 组官方化合并映射（review_alias_merge.json）；不存在则跳过",
    )
    parser.add_argument("--no-review-merge", action="store_true", help="不应用 review 组合并")
    parser.add_argument("--version", default=CLEAN_VERSION, help="清洗版本号（写入 summary）")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    triples_path = Path(args.triples)
    candidates_path = Path(args.candidates)
    output_dir = Path(args.output_dir).expanduser()
    for path in (triples_path, candidates_path):
        if not path.is_file():
            raise SystemExit(f"输入不存在: {path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    alias_map, auto_groups, conflicts = load_auto_alias_map(candidates_path)
    review_path = None if args.no_review_merge else Path(args.review_merge)
    review_map = load_review_merge(review_path)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"自动别名 {len(alias_map)} 对；review 合并 {len(review_map)} 对（{review_path}）")

    stats = CleanStats()
    output_triples = output_dir / "triples_clean_v2.jsonl"
    clean_file(triples_path, output_triples, alias_map, stats, limit=args.limit, review_map=review_map)
    if not output_triples.exists() or stats.triples == 0:
        raise SystemExit("清洗失败：没有输出三元组")

    write_alias_file(
        output_dir / "实体别名映射.json",
        alias_map,
        auto_groups,
        conflicts,
        stats,
        generated_at,
        review_map=review_map,
    )
    write_changes_csv(output_dir / "name_cleaning_changes.csv.gz", stats)
    verification = verify_output(output_triples, alias_map, stats.triples, review_map)

    summary: dict[str, Any] = {
        "version": args.version,
        "generated_at": generated_at,
        "source_triples": str(triples_path),
        "output_triples": str(output_triples),
        "decisions": {
            "rule_a": "normalize_for_compare_only",
            "rule_b": "empty_to_unknown",
            "rule_c": "unknown_to_unknown_cn",
            "rule_d": "auto_groups_only",
            "rule_e": "review_groups_officialized" if review_map else "disabled",
        },
        "review_merge_pairs": len(review_map),
        "review_merge_source": str(review_path) if review_map else None,
        "triples": stats.triples,
        "mentions": stats.mentions,
        "auto_groups": len(auto_groups),
        "alias_mapping_pairs": len(alias_map),
        "conflict_names": len(conflicts),
        "by_rule": {
            "A_whitespace": stats.by_rule.get(RULE_A_WHITESPACE, 0),
            "B_empty_to_unknown": stats.by_rule.get(RULE_B_EMPTY, 0),
            "C_unknown_to_unknown_cn": stats.by_rule.get(RULE_C_UNKNOWN, 0),
            "D_auto_alias": stats.by_rule.get(RULE_D_ALIAS, 0),
            "E_review_alias": stats.by_rule.get(RULE_E_REVIEW, 0),
        },
        "verification": verification,
        "outputs": {
            name: {"size_bytes": (output_dir / name).stat().st_size}
            for name in (
                "triples_clean_v2.jsonl",
                "实体别名映射.json",
                "name_cleaning_changes.csv.gz",
            )
        },
    }
    (output_dir / "name_cleaning_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "name_cleaning_report.md").write_text(
        build_report(summary, stats, verification, conflicts, auto_groups), encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
