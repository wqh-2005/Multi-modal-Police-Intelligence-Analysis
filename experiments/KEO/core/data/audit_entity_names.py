from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


from .paths import NAME_CLEANING_DIR, PERSON_A_DIR, REPO_ROOT

DEFAULT_ROOT = REPO_ROOT
DEFAULT_TRIPLES = PERSON_A_DIR / "triples_clean.jsonl"
DEFAULT_OUTPUT = NAME_CLEANING_DIR

SHARED_TYPES = {"身份角色", "机构", "应用程序", "平台"}
PLACEHOLDER_EXACT = {
    "",
    "unknown",
    "未知",
    "不详",
    "未知人员",
    "未知机构",
    "某机构",
    "某应用",
    "某app",
    "某平台",
    "app会议视频",
}
MANUAL_SYNONYMS = [
    ("平台", "微信", ["wechat"]),
    ("平台", "支付宝", ["alipay"]),
    ("平台", "抖音", ["douyin"]),
    ("平台", "快手", ["kuaishou"]),
    ("平台", "QQ", ["qq"]),
]


def normalize_name(name: str) -> str:
    value = unicodedata.normalize("NFKC", name)
    value = value.replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value.casefold()


def nfkc_changed(name: str) -> bool:
    return unicodedata.normalize("NFKC", name) != name


def whitespace_changed(name: str) -> bool:
    return bool(
        name != name.strip()
        or re.search(r"\s{2,}", name)
        or "\t" in name
        or "\n" in name
        or "\r" in name
    )


def is_placeholder(name: str) -> bool:
    folded = normalize_name(name)
    if folded in PLACEHOLDER_EXACT:
        return True
    return folded.startswith("未知") or folded.startswith("某")


GENERIC_NAMES = {
    "",
    "app",
    "软件",
    "平台",
    "客户端",
    "聊天",
    "聊天软件",
    "聊天app",
    "聊天平台",
    "语聊",
    "其他",
    "其他软件",
    "其他app",
    "对方",
    "对方软件",
    "对方app",
    "对方提供的app",
    "对方提供的软件",
    "对方指定的app",
    "对方指定的软件",
    "网络",
    "网络app",
    "游戏",
    "游戏软件",
    "一款",
    "一款软件",
    "做任务的软件",
    "做任务的app",
    "刷单的app",
    "刷单的软件",
    "涉嫌软件",
    "涉嫌app",
    "无名称",
    "无名称app",
    "网站",
    "网站平台",
    "网页",
    "网页平台",
    "网上",
    "网上平台",
    "视频会议",
    "视频会议软件",
    "手机银行",
    "客服",
    "安全防护",
    "交易所",
    "交易所软件",
    "炒股",
    "炒股软件",
    "炒股app",
    "投资",
    "投资app",
    "理财",
    "理财app",
    "贷款",
    "贷款app",
    "借款",
    "借款app",
    "兼职",
    "兼职app",
    "购物",
    "购物软件",
    "购物app",
    "直播",
    "直播软件",
    "直播app",
    "短视频",
    "短视频app",
    "浏览器",
    "浏览器app",
    "输入法",
    "商城",
    "商城app",
    "保险",
    "保险app",
    "银行",
    "银行app",
    "证券",
    "证券app",
    "股票",
    "股票app",
    "期货",
    "期货app",
    "会议",
    "会议软件",
    "会议app",
    "云会议",
    "云会议app",
    "反诈",
    "反诈app",
    "聘",
    "聘软件",
    "招聘",
    "招聘app",
    "交友",
    "交友软件",
    "交友app",
    "婚恋",
    "婚恋app",
    "相亲",
    "相亲软件",
    "相亲app",
    "社交",
    "社交软件",
    "社交app",
    "支付",
    "支付app",
    "钱包",
    "钱包app",
}


def is_generic_name(name: str) -> bool:
    value = normalize_name(name)
    if value in GENERIC_NAMES or is_placeholder(name):
        return True
    stripped = re.sub(r"(app|软件|客户端|平台)$", "", value).strip()
    return stripped in GENERIC_NAMES


def md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(md_cell(item) for item in headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(md_cell(item) for item in row) + " |")
    return "\n".join(lines)


def org_key(name: str) -> str:
    value = normalize_name(name)
    value = re.sub(r"(股份有限公司|有限公司)$", "", value)
    if value.startswith("中国") and value != "中国银行" and len(value) > 4:
        value = value[2:]
    return value


def product_key(name: str) -> str:
    value = normalize_name(name)
    value = re.sub(r"(app|软件|客户端|平台)$", "", value)
    return value.strip()


def sort_members(members: list[tuple[str, int]]) -> list[tuple[str, int]]:
    return sorted(members, key=lambda item: (-item[1], item[0]))


def choose_canonical(members: list[tuple[str, int]]) -> str:
    return sort_members(members)[0][0]


def build_group(
    entity_type: str,
    members: list[tuple[str, int]],
    reason: str,
    confidence: str,
    auto_merge: bool,
) -> dict[str, Any]:
    members = sort_members(members)
    return {
        "entity_type": entity_type,
        "canonical_candidate": choose_canonical(members),
        "members": [{"name": name, "mentions": mentions} for name, mentions in members],
        "member_mentions": sum(mentions for _, mentions in members),
        "reason": reason,
        "confidence": confidence,
        "auto_merge": auto_merge,
    }


def add_group(
    output: list[dict[str, Any]],
    seen: set[tuple[str, frozenset[str]]],
    group: dict[str, Any],
) -> None:
    signature = (group["entity_type"], frozenset(item["name"] for item in group["members"]))
    if len(signature[1]) < 2 or signature in seen:
        return
    seen.add(signature)
    group["generic_risk"] = any(
        is_generic_name(item["name"]) for item in group["members"]
    )
    if group["reason"].startswith("NFKC"):
        group["recommended_action"] = (
            "auto" if group["auto_merge"] and not group["generic_risk"] else "hold"
        )
    elif group["entity_type"] in {"机构", "平台"}:
        group["recommended_action"] = "hold" if group["generic_risk"] else "auto"
    elif group["entity_type"] == "应用程序":
        if group["generic_risk"]:
            group["recommended_action"] = "hold"
        elif group["member_mentions"] >= 100:
            group["recommended_action"] = "auto"
        elif group["member_mentions"] >= 10:
            group["recommended_action"] = "review"
        else:
            group["recommended_action"] = "hold"
    else:
        group["recommended_action"] = "hold"
    if group["recommended_action"] == "auto":
        group["apply_priority"] = 2 if group["reason"].startswith(("机构", "产品", "平台")) else 1
    else:
        group["apply_priority"] = 0
    output.append(group)


def main() -> None:
    parser = argparse.ArgumentParser(description="审计人员 A 三元组中的实体名称，不修改数据")
    parser.add_argument("--triples", default=str(DEFAULT_TRIPLES))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    triples_path = Path(args.triples)
    output_dir = Path(args.output)
    if not triples_path.is_file():
        raise SystemExit(f"输入不存在: {triples_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    type_mentions: Counter[str] = Counter()
    name_mentions: Counter[str] = Counter()
    name_types: dict[str, set[str]] = defaultdict(set)
    empty_samples: dict[str, list[tuple[str, str]]] = defaultdict(list)
    records = 0
    mentions = 0

    with triples_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            records += 1
            source_id = record.get("source_id", "")
            triple_id = record.get("triple_id", "")
            for side in ("head", "tail"):
                entity = record[side]
                name = entity.get("name") or ""
                entity_type = entity.get("type") or ""
                entity_id = entity.get("id") or ""
                key = (entity_type, name)
                item = groups.get(key)
                if item is None:
                    item = {
                        "mentions": 0,
                        "ids": set(),
                        "sample_source_id": source_id,
                        "sample_triple_id": triple_id,
                        "nfkc_changed_mentions": 0,
                        "whitespace_changed_mentions": 0,
                    }
                    groups[key] = item
                item["mentions"] += 1
                if entity_id:
                    item["ids"].add(entity_id)
                if nfkc_changed(name):
                    item["nfkc_changed_mentions"] += 1
                if whitespace_changed(name):
                    item["whitespace_changed_mentions"] += 1
                type_mentions[entity_type] += 1
                name_mentions[name] += 1
                name_types[name].add(entity_type)
                mentions += 1
                if not name.strip() and len(empty_samples[entity_type]) < 5:
                    empty_samples[entity_type].append((source_id, triple_id))

    unique_names = len(name_types)
    cross_type_names = [
        name for name, types in name_types.items() if name.strip() and len(types) > 1
    ]
    cross_type_names.sort(key=lambda name: (-name_mentions[name], name))

    empty_pairs = [(key, item) for key, item in groups.items() if not key[1].strip()]
    empty_mentions = sum(item["mentions"] for _, item in empty_pairs)
    empty_count_by_type: Counter[str] = Counter()
    for (entity_type, name), item in empty_pairs:
        empty_count_by_type[entity_type] += item["mentions"]
    placeholder_pairs = [
        (key, item) for key, item in groups.items() if is_placeholder(key[1])
    ]
    placeholder_mentions = sum(item["mentions"] for _, item in placeholder_pairs)
    normalization_pairs = [
        (key, item) for key, item in groups.items() if normalize_name(key[1]) != key[1]
    ]
    normalization_mentions = sum(item["mentions"] for _, item in normalization_pairs)
    nfkc_pairs = [
        (key, item) for key, item in groups.items() if item["nfkc_changed_mentions"] > 0
    ]
    nfkc_mentions = sum(item["nfkc_changed_mentions"] for _, item in nfkc_pairs)
    whitespace_pairs = [
        (key, item) for key, item in groups.items() if item["whitespace_changed_mentions"] > 0
    ]
    whitespace_mentions = sum(
        item["whitespace_changed_mentions"] for _, item in whitespace_pairs
    )
    unknown_variant_mentions = sum(
        item["mentions"]
        for (_, name), item in groups.items()
        if name.strip().casefold() in {"unknown"}
    )
    unknown_cn_mentions = sum(
        item["mentions"] for (_, name), item in groups.items() if name.strip() == "未知"
    )

    # 1. Exhaustive audit CSV.
    audit_path = output_dir / "name_cleaning_audit.csv"
    with audit_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "entity_type",
                "raw_name",
                "mentions",
                "unique_entity_ids",
                "normalized_name",
                "normalization_changed",
                "nfkc_changed_mentions",
                "whitespace_changed_mentions",
                "is_empty",
                "is_placeholder",
                "is_generic",
                "is_shared_type",
                "other_types",
                "sample_source_id",
                "sample_triple_id",
            ],
        )
        writer.writeheader()
        for (entity_type, name), item in sorted(
            groups.items(), key=lambda pair: (pair[0][0], -pair[1]["mentions"], pair[0][1])
        ):
            other_types = sorted(name_types[name] - {entity_type})
            writer.writerow(
                {
                    "entity_type": entity_type,
                    "raw_name": name,
                    "mentions": item["mentions"],
                    "unique_entity_ids": len(item["ids"]),
                    "normalized_name": normalize_name(name),
                    "normalization_changed": int(normalize_name(name) != name),
                    "nfkc_changed_mentions": item["nfkc_changed_mentions"],
                    "whitespace_changed_mentions": item["whitespace_changed_mentions"],
                    "is_empty": int(not name.strip()),
                    "is_placeholder": int(is_placeholder(name)),
                    "is_generic": int(is_generic_name(name)),
                    "is_shared_type": int(entity_type in SHARED_TYPES),
                    "other_types": ";".join(other_types),
                    "sample_source_id": item["sample_source_id"],
                    "sample_triple_id": item["sample_triple_id"],
                }
            )

    # 2. Alias candidates.
    alias_groups: list[dict[str, Any]] = []
    seen_groups: set[tuple[str, frozenset[str]]] = set()

    normalized_groups: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for (entity_type, name), item in groups.items():
        if entity_type not in SHARED_TYPES or not name.strip():
            continue
        normalized_groups[(entity_type, normalize_name(name))].append((name, item["mentions"]))
    for (entity_type, _), members in sorted(
        normalized_groups.items(), key=lambda pair: (-sum(item[1] for item in pair[1]), pair[0])
    ):
        if len(members) < 2:
            continue
        names = [name for name, _ in members]
        auto_merge = not any(is_placeholder(name) for name in names)
        group = build_group(
            entity_type,
            members,
            "NFKC、空白或大小写归一后相同",
            "high" if auto_merge else "medium",
            auto_merge,
        )
        add_group(alias_groups, seen_groups, group)

    for entity_type, key_func, reason in (
        ("机构", org_key, "机构名称仅差“中国”前缀或公司后缀"),
        ("应用程序", product_key, "产品名称仅差 APP/软件/客户端/平台后缀"),
        ("平台", product_key, "平台名称仅差 APP/软件/客户端/平台后缀"),
    ):
        buckets: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for (group_type, name), item in groups.items():
            if group_type != entity_type or not name.strip() or is_placeholder(name):
                continue
            key = key_func(name)
            if not key or key in {"app", "软件", "客户端", "平台"}:
                continue
            buckets[key].append((name, item["mentions"]))
        for key, members in sorted(
            buckets.items(), key=lambda pair: (-sum(item[1] for item in pair[1]), pair[0])
        ):
            if len(members) < 2:
                continue
            group = build_group(entity_type, members, reason, "medium", False)
            add_group(alias_groups, seen_groups, group)

    for entity_type, canonical, aliases in MANUAL_SYNONYMS:
        wanted = {normalize_name(canonical), *(normalize_name(alias) for alias in aliases)}
        members = [
            (name, item["mentions"])
            for (group_type, name), item in groups.items()
            if group_type == entity_type and normalize_name(name) in wanted
        ]
        if len(members) >= 2:
            group = build_group(
                entity_type,
                members,
                "常见中英文别名候选，需人工确认",
                "medium",
                False,
            )
            add_group(alias_groups, seen_groups, group)

    alias_groups.sort(
        key=lambda group: (
            group["entity_type"],
            -group["member_mentions"],
            group["canonical_candidate"],
        )
    )
    prefix_counts: dict[str, int] = {"exact": 0, "contain": 0, "manual": 0}
    for prefix, group in (
        [("exact", group) for group in alias_groups if group["reason"].startswith("NFKC")]
        + [("contain", group) for group in alias_groups if group["reason"].startswith(("机构", "产品", "平台"))]
        + [("manual", group) for group in alias_groups if group["reason"].startswith("常见")]
    ):
        prefix_counts[prefix] += 1
        group["group_id"] = f"{prefix}_{prefix_counts[prefix]:04d}"

    auto_canonicals: dict[tuple[str, str], set[str]] = defaultdict(set)
    for group in alias_groups:
        if group["recommended_action"] != "auto":
            continue
        for member in group["members"]:
            auto_canonicals[(group["entity_type"], member["name"])].add(
                group["canonical_candidate"]
            )
    auto_conflict_names = sum(
        1 for canonicals in auto_canonicals.values() if len(canonicals) > 1
    )

    alias_payload = {
        "version": "0.1-audit",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_triples": str(triples_path),
        "status": "candidate_only",
        "notes": [
            "本文件是候选表，不是已确认的正式别名映射。",
            "每个组都有 recommended_action：auto 表示建议直接采用，review 表示需人工复核，hold 表示暂不合并。",
            "exact 组只做 NFKC、空白和大小写归一；其中非泛化、非占位名称的组建议 auto。",
            "contain 组中，机构/平台的非泛化名称建议 auto；应用程序仅在组内提及 >=100 且非泛化时建议 auto，10~99 建议 review，其余 hold。",
            "泛化名称和占位名称（APP、软件、聊天、未知、某APP 等）一律 hold，不作为共享概念。",
            "同一名称可能同时出现在 exact 和 contain 组；执行时按 apply_priority 处理，contain auto（2）优先于 exact auto（1）。",
            "人员不跨案合并；身份角色不做包含关系自动合并；不同实体类型之间不合并。",
        ],
        "groups": alias_groups,
    }
    alias_path = output_dir / "entity_alias_candidates.json"
    alias_path.write_text(
        json.dumps(alias_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 3. Summary JSON.
    summary = {
        "version": "0.1-audit",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_triples": str(triples_path),
        "records": records,
        "mentions": mentions,
        "types": dict(type_mentions.most_common()),
        "unique_type_name_pairs": len(groups),
        "unique_names": unique_names,
        "empty_pairs": len(empty_pairs),
        "empty_mentions": empty_mentions,
        "whitespace_pairs": len(whitespace_pairs),
        "whitespace_mentions": whitespace_mentions,
        "nfkc_pairs": len(nfkc_pairs),
        "nfkc_mentions": nfkc_mentions,
        "normalization_pairs": len(normalization_pairs),
        "normalization_mentions": normalization_mentions,
        "placeholder_pairs": len(placeholder_pairs),
        "placeholder_mentions": placeholder_mentions,
        "unknown_variant_mentions": unknown_variant_mentions,
        "unknown_cn_mentions": unknown_cn_mentions,
        "cross_type_names": len(cross_type_names),
        "shared_type_alias_groups": len(alias_groups),
        "exact_alias_groups": sum(1 for group in alias_groups if group["reason"].startswith("NFKC")),
        "containment_alias_groups": sum(1 for group in alias_groups if group["reason"].startswith(("机构", "产品", "平台"))),
        "manual_alias_groups": sum(1 for group in alias_groups if group["reason"].startswith("常见")),
        "generic_risk_groups": sum(1 for group in alias_groups if group["generic_risk"]),
        "recommended_auto_groups": sum(1 for group in alias_groups if group["recommended_action"] == "auto"),
        "recommended_review_groups": sum(1 for group in alias_groups if group["recommended_action"] == "review"),
        "recommended_hold_groups": sum(1 for group in alias_groups if group["recommended_action"] == "hold"),
        "auto_conflict_names": auto_conflict_names,
    }
    summary_path = output_dir / "name_cleaning_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 4. Rules document.
    top_overall = sorted(groups.items(), key=lambda pair: -pair[1]["mentions"])[:20]
    top_by_type_rows: list[list[Any]] = []
    for entity_type, _ in type_mentions.most_common():
        items = [
            (name, item)
            for (group_type, name), item in groups.items()
            if group_type == entity_type
        ]
        items.sort(key=lambda pair: -pair[1]["mentions"])
        for name, item in items[:5]:
            top_by_type_rows.append(
                [entity_type, name, item["mentions"], len(item["ids"])]
            )

    fullwidth_rows = [
        [entity_type, name, item["nfkc_changed_mentions"], item["mentions"]]
        for (entity_type, name), item in sorted(
            nfkc_pairs, key=lambda pair: -pair[1]["nfkc_changed_mentions"]
        )[:20]
    ]
    whitespace_rows = [
        [entity_type, repr(name), item["whitespace_changed_mentions"], item["mentions"]]
        for (entity_type, name), item in sorted(
            whitespace_pairs, key=lambda pair: -pair[1]["whitespace_changed_mentions"]
        )
    ]
    placeholder_rows = [
        [entity_type, name, item["mentions"], len(item["ids"])]
        for (entity_type, name), item in sorted(
            [
                pair
                for pair in placeholder_pairs
                if pair[0][1].strip()
            ],
            key=lambda pair: -pair[1]["mentions"],
        )[:25]
    ]
    cross_type_rows = [
        [name, "、".join(sorted(name_types[name])), name_mentions[name]]
        for name in cross_type_names[:20]
    ]
    multi_id_rows = [
        [entity_type, name, len(item["ids"]), item["mentions"]]
        for (entity_type, name), item in sorted(
            groups.items(), key=lambda pair: -len(pair[1]["ids"])
        )[:20]
    ]
    empty_rows = [
        [
            entity_type,
            count,
            "、".join(
                f"`{triple_id}`" for _, triple_id in empty_samples.get(entity_type, [])[:3]
            ),
        ]
        for entity_type, count in empty_count_by_type.most_common()
    ]
    exact_rows = [
        [
            group["entity_type"],
            group["canonical_candidate"],
            f"{group['member_mentions']:,}",
            group["recommended_action"],
            " / ".join(item["name"] for item in group["members"]),
        ]
        for group in alias_groups
        if group["reason"].startswith("NFKC")
    ][:30]
    contain_rows = [
        [
            group["entity_type"],
            group["canonical_candidate"],
            f"{group['member_mentions']:,}",
            group["recommended_action"],
            " / ".join(item["name"] for item in group["members"]),
        ]
        for group in alias_groups
        if group["reason"].startswith(("机构", "产品", "平台"))
    ][:30]
    manual_rows = [
        [
            group["entity_type"],
            group["canonical_candidate"],
            f"{group['member_mentions']:,}",
            group["recommended_action"],
            " / ".join(item["name"] for item in group["members"]),
        ]
        for group in alias_groups
        if group["reason"].startswith("常见")
    ]
    identity_rows = [
        [name, item["mentions"], len(item["ids"])]
        for (entity_type, name), item in sorted(
            [
                pair
                for pair in groups.items()
                if pair[0][0] == "身份角色"
            ],
            key=lambda pair: -pair[1]["mentions"],
        )[:12]
    ]
    person_rows = [
        [name, item["mentions"], len(item["ids"])]
        for (entity_type, name), item in sorted(
            [
                pair
                for pair in groups.items()
                if pair[0][0] == "人员"
            ],
            key=lambda pair: -pair[1]["mentions"],
        )[:12]
    ]

    lines: list[str] = []
    lines.append("# 人员 A 实体名称清洗审计")
    lines.append("")
    lines.append("> 版本：`0.1-audit`  ")
    lines.append(f"> 生成时间：{summary['generated_at']}  ")
    lines.append(f"> 输入：`{triples_path}`  ")
    lines.append("> 状态：**只审计，不修改任何三元组**  ")
    lines.append("> 结论：名称清洗规则和别名映射需要确认后，才能进入实际清洗。")
    lines.append("")
    lines.append("## 1. 范围与总量")
    lines.append("")
    lines.append(f"- 三元组：{records:,} 条")
    lines.append(f"- 实体提及：{mentions:,} 次（head + tail）")
    lines.append(f"- 唯一 `(类型, 名称)` 组合：{len(groups):,}")
    lines.append(f"- 唯一名称：{unique_names:,}")
    lines.append(f"- 实体类型：{len(type_mentions)} 类")
    lines.append("")
    lines.append("## 2. 类型分布")
    lines.append("")
    type_rows = [[entity_type, count] for entity_type, count in type_mentions.most_common()]
    lines.append(md_table(["实体类型", "提及次数"], type_rows))
    lines.append("")
    lines.append("## 3. 名称问题")
    lines.append("")
    lines.append("### 3.1 空名称")
    lines.append("")
    lines.append(f"- 空名称提及：{empty_mentions:,} 次")
    lines.append(f"- 涉及 `(类型, 空名称)` 组合：{len(empty_pairs)} 个")
    lines.append("")
    if empty_rows:
        lines.append(md_table(["实体类型", "空名称提及", "样例 triple_id"], empty_rows))
    lines.append("")
    lines.append("### 3.2 空白与全半角")
    lines.append("")
    lines.append(f"- 含首尾/连续空白：{whitespace_mentions:,} 次，涉及 {len(whitespace_pairs)} 个组合")
    lines.append(f"- NFKC 会改变的名称提及：{nfkc_mentions:,} 次，涉及 {len(nfkc_pairs)} 个组合")
    lines.append(f"- 归一化后会变化的名称提及：{normalization_mentions:,} 次，涉及 {len(normalization_pairs)} 个组合")
    lines.append("")
    if whitespace_rows:
        lines.append("空白样例：")
        lines.append("")
        lines.append(md_table(["实体类型", "名称", "空白提及", "总提及"], whitespace_rows))
        lines.append("")
    if fullwidth_rows:
        lines.append("全半角样例，按受影响提及排序：")
        lines.append("")
        lines.append(md_table(["实体类型", "名称", "NFKC 改变提及", "总提及"], fullwidth_rows))
        lines.append("")
    lines.append("### 3.3 占位名称")
    lines.append("")
    lines.append(f"- 占位名称提及：{placeholder_mentions:,} 次")
    lines.append(f"- 其中 `UNKNOWN` / `unknown`：{unknown_variant_mentions:,} 次")
    lines.append(f"- 其中中文 `未知`：{unknown_cn_mentions:,} 次")
    lines.append("")
    lines.append(md_table(["实体类型", "名称", "提及", "唯一实体 ID"], placeholder_rows))
    lines.append("")
    lines.append("### 3.4 同名跨类型")
    lines.append("")
    lines.append(f"- 同名出现在多个类型下：{len(cross_type_names):,} 个名称")
    lines.append("- 这些名称**不能跨类型合并**；类型是共享概念键的一部分。")
    lines.append("")
    lines.append(md_table(["名称", "出现类型", "总提及"], cross_type_rows))
    lines.append("")
    lines.append("### 3.5 同名多实体 ID")
    lines.append("")
    lines.append("下表用于判断哪些同名 ID 多。人员即使同名也不能跨案合并；共享概念才可按名称合并。")
    lines.append("")
    lines.append(md_table(["实体类型", "名称", "唯一实体 ID", "提及"], multi_id_rows))
    lines.append("")
    lines.append("## 4. 共享类型别名候选")
    lines.append("")
    lines.append("仅覆盖 `身份角色`、`机构`、`应用程序`、`平台`。人员不在此范围。")
    lines.append("")
    lines.append("### 4.1 归一化后相同（高置信）")
    lines.append("")
    lines.append(f"共 {summary['exact_alias_groups']} 组；这些差异只来自 NFKC、空白或大小写。")
    lines.append("")
    lines.append(
        f"所有类型合计建议直接采用 `auto` 的组：{summary['recommended_auto_groups']} 组；"
        "exact 组中泛化名称或占位名称标记为 `hold`，不能自动合并。"
    )
    lines.append("")
    if exact_rows:
        lines.append(md_table(["类型", "建议规范名", "涉及提及", "推荐动作", "成员"], exact_rows))
    lines.append("")
    lines.append("### 4.2 词面包含候选（需人工确认）")
    lines.append("")
    lines.append(f"共 {summary['containment_alias_groups']:,} 组；包括机构“中国”前缀/公司后缀、产品 APP/软件后缀。")
    lines.append("")
    lines.append(
        "建议策略：机构、平台的非泛化名称可批量采用；应用程序仅在组内提及 ≥100 且非泛化时采用，"
        "10～99 进入人工复核，其余暂不合并。"
    )
    lines.append("")
    if contain_rows:
        lines.append(md_table(["类型", "建议规范名", "涉及提及", "推荐动作", "成员"], contain_rows))
    lines.append("")
    lines.append("### 4.3 中英文别名候选（需人工确认）")
    lines.append("")
    lines.append(f"共 {summary['manual_alias_groups']} 组。")
    lines.append("")
    if manual_rows:
        lines.append(md_table(["类型", "建议规范名", "涉及提及", "推荐动作", "成员"], manual_rows))
    else:
        lines.append("当前数据中没有同时出现足够的中英文别名对。")
    lines.append("")
    lines.append("## 5. 不建议合并的情况")
    lines.append("")
    lines.append("### 5.1 人员")
    lines.append("")
    lines.append("人员按 `(source_id, entity.id)` 建节点，**同名也不跨案合并**。")
    lines.append("")
    lines.append(md_table(["名称", "提及", "唯一实体 ID"], person_rows))
    lines.append("")
    lines.append("### 5.2 身份角色")
    lines.append("")
    lines.append("`客服`、`京东客服`、`电商物流客服`等是不同粒度的身份概念，不能因为包含关系自动合并。")
    lines.append("")
    lines.append(md_table(["名称", "提及", "唯一实体 ID"], identity_rows))
    lines.append("")
    lines.append("### 5.3 其他禁止项")
    lines.append("")
    lines.append("- 不同实体类型之间不合并；")
    lines.append("- `微信` 与 `微信账号` 不合并；")
    lines.append("- `微信` 与 `微信微粒贷` 不当作同义词；")
    lines.append("- 泛化名称 `APP`、`软件`、`平台`、`网址链接` 不作为共享概念；")
    lines.append("- 空名称、`UNKNOWN`、`未知`、`某APP` 等保留为案件内节点。")
    lines.append("")
    lines.append("## 6. 建议清洗规则（待确认）")
    lines.append("")
    lines.append("### 规则 A：确定性归一化")
    lines.append("")
    lines.append("对所有 `head.name` / `tail.name` 执行：")
    lines.append("")
    lines.append("1. Unicode NFKC；")
    lines.append("2. 去首尾空白；")
    lines.append("3. 连续空白折叠为一个空格；")
    lines.append("4. 拉丁字母统一大小写用于比较，显示名保留最高频写法。")
    lines.append("")
    lines.append(f"影响（归一化键变化）：{normalization_mentions:,} 次提及，{len(normalization_pairs):,} 个 `(类型, 名称)` 组合。")
    lines.append("")
    lines.append(
        f"其中实际会改变显示名的 NFKC 变化：{nfkc_mentions:,} 次；空白变化：{whitespace_mentions:,} 次。"
        "大小写只用于比较，不改变显示名。"
    )
    lines.append("")
    lines.append("### 规则 B：空名称")
    lines.append("")
    lines.append(f"当前 {empty_mentions:,} 次空名称。建议二选一：")
    lines.append("")
    lines.append("- B1：保留空字符串，由 C 按 `LOCAL_ONLY_NAMES` 处理；")
    lines.append("- B2：统一写成 `未知`，保留原值为空的事实。")
    lines.append("")
    lines.append("建议 B2，因为格式 1.1 要求 `name` 必填，且 C 已把 `未知` 视为案件内节点。")
    lines.append("")
    lines.append("### 规则 C：UNKNOWN 统一")
    lines.append("")
    lines.append(f"建议把 `UNKNOWN` / `unknown` 统一为 `未知`，涉及 {unknown_variant_mentions:,} 次提及。")
    lines.append("")
    lines.append("### 规则 D：共享别名")
    lines.append("")
    lines.append("- 只应用 `recommended_action = auto` 的组；")
    lines.append("- `review` 组保留候选，不自动合并；")
    lines.append("- `hold` 组不合并，包括所有泛化名称和占位名称；")
    lines.append("- `exact` 组中非泛化、非占位的名称可自动采用；")
    lines.append("- `contain` 组中，机构、平台的非泛化名称可自动采用；应用程序仅 ≥100 次提及的非泛化组自动采用，10～99 次进入 `review`；")
    lines.append("- `manual` 组为 0，当前没有足够的中英文别名对；")
    lines.append(
        f"- 同一名称可能同时出现在 exact 和 contain 组；当前有 {auto_conflict_names:,} 个名称存在多个 auto 规范名候选，执行时按 `apply_priority` 处理，contain auto（2）优先于 exact auto（1）；"
    )
    lines.append("- 只修改 `name`，保留原值到 `original_name` 或映射表；")
    lines.append("- 不改变 `head.id` / `tail.id`，不改变人员节点范围。")
    lines.append("")
    lines.append("### 规则 E：人员与类型边界")
    lines.append("")
    lines.append("- 人员不跨案合并；")
    lines.append("- 不同 `type` 不合并；")
    lines.append("- 身份角色不做包含关系自动合并；")
    lines.append("- 所有未确认名称保留原样并记录。")
    lines.append("")
    lines.append("## 7. 影响评估")
    lines.append("")
    lines.append("| 规则 | 影响提及 | 影响组合/组数 |")
    lines.append("|---|---:|---:|")
    lines.append(f"| A 确定性归一化 | {normalization_mentions:,} | {len(normalization_pairs):,} 组合 |")
    lines.append(f"| B 空名称补 `未知` | {empty_mentions:,} | {len(empty_pairs)} 组合 |")
    lines.append(f"| C `UNKNOWN` → `未知` | {unknown_variant_mentions:,} | 待统计 |")
    lines.append(
        f"| D 自动别名（auto） | {sum(group['member_mentions'] for group in alias_groups if group['recommended_action'] == 'auto'):,} | {summary['recommended_auto_groups']:,} 组 |"
    )
    lines.append(
        f"| D 待复核（review） | {sum(group['member_mentions'] for group in alias_groups if group['recommended_action'] == 'review'):,} | {summary['recommended_review_groups']:,} 组 |"
    )
    lines.append(
        f"| D 暂不合并（hold） | {sum(group['member_mentions'] for group in alias_groups if group['recommended_action'] == 'hold'):,} | {summary['recommended_hold_groups']:,} 组 |"
    )
    lines.append("")
    lines.append(
        "规则之间、别名组之间的提及可能重叠（例如 `QQ` 同时出现在 exact 与 contain 组），"
        "不能直接相加；实际清洗时按名称或按组去重。"
    )
    lines.append("")
    lines.append("## 8. 输出文件")
    lines.append("")
    lines.append("- `name_cleaning_audit.csv`：全部 `(类型, 名称)` 的逐行审计，含 `is_generic`；")
    lines.append("- `entity_alias_candidates.json`：共享类型别名候选，含 `recommended_action` 和 `generic_risk`；")
    lines.append("- `name_cleaning_summary.json`：机器可读汇总；")
    lines.append("- `name_cleaning_rules.md`：本文件。")
    lines.append("")
    lines.append("## 9. 下一步")
    lines.append("")
    lines.append("1. 确认规则 A/B/C 和 `auto` / `review` / `hold` 策略；")
    lines.append("2. 如需调整，先改策略再执行，不直接改 100 万条三元组；")
    lines.append("3. 执行清洗，保留 `original_name`；")
    lines.append("4. 重新导出 KEO 包并复验数量、ID、划分不变；")
    lines.append("5. 再进入冻结。")
    lines.append("")

    rules_path = output_dir / "name_cleaning_rules.md"
    rules_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "output": str(output_dir),
                "records": records,
                "mentions": mentions,
                "unique_type_name_pairs": len(groups),
                "unique_names": unique_names,
                "empty_mentions": empty_mentions,
                "whitespace_mentions": whitespace_mentions,
                "nfkc_mentions": nfkc_mentions,
                "normalization_mentions": normalization_mentions,
                "placeholder_mentions": placeholder_mentions,
                "cross_type_names": len(cross_type_names),
                "alias_groups": len(alias_groups),
                "exact_alias_groups": summary["exact_alias_groups"],
                "containment_alias_groups": summary["containment_alias_groups"],
                "manual_alias_groups": summary["manual_alias_groups"],
                "generic_risk_groups": summary["generic_risk_groups"],
                "recommended_auto_groups": summary["recommended_auto_groups"],
                "recommended_review_groups": summary["recommended_review_groups"],
                "recommended_hold_groups": summary["recommended_hold_groups"],
                "auto_conflict_names": summary["auto_conflict_names"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
