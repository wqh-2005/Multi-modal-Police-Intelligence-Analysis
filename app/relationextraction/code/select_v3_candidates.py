from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SEED = 20260928
DEFAULT_PER_SOURCE = 300
RELATION_HINT_TARGET = 15
CATEGORY_MINIMUM = 15

# 这些规则只用于“候选召回”，最终关系仍必须由LLM抽取并经人工审核。
RELATION_PATTERNS: dict[str, list[str]] = {
    "冒充": [r"自称", r"冒充", r"谎称", r"假扮", r"声称.{0,12}(客服|民警|警察|领导|工作人员)"],
    "联系": [r"联系", r"来电", r"接到.{0,8}电话", r"打电话", r"通话", r"私信"],
    "添加好友": [r"添加.{0,8}好友", r"加.{0,5}(微信|QQ|好友)", r"好友申请", r"通过.{0,8}好友"],
    "邀请入群": [r"拉.{0,6}(进|入).{0,8}群", r"邀请.{0,10}(进群|入群|群聊)", r"加入.{0,8}(微信群|QQ群|群聊)"],
    "威胁": [r"威胁", r"恐吓", r"不.{0,15}就.{0,15}(冻结|抓捕|拘留|曝光|起诉|报警)", r"冻结.{0,8}账户", r"影响.{0,5}征信", r"通缉", r"抓捕"],
    "发送": [r"发送", r"发来", r"发给", r"传来", r"二维码", r"链接", r"截图"],
    "提供": [r"提供.{0,15}(账户|账号|卡号|二维码|链接|网址|电话)", r"给.{0,8}(账号|账户|银行卡号|二维码|链接)"],
    "要求下载": [r"(要求|让|叫|需|必须).{0,12}(下载|安装)", r"按照.{0,10}(指示|要求).{0,12}下载"],
    "下载": [r"下载了", r"下载安装", r"安装了", r"下载.{0,20}(APP|软件|应用)", r"安装.{0,20}(APP|软件|应用)"],
    "要求点击": [r"(要求|让|叫).{0,12}(点击|点开|打开).{0,12}(链接|网址|页面|按钮)", r"按.{0,8}提示.{0,8}(点击|点开)"],
    "点击": [r"点击了", r"点开了", r"打开了.{0,10}(链接|网址|页面)", r"点击.{0,10}(链接|网址|按钮)"],
    "要求提供信息": [r"(要求|让|叫|需).{0,18}(提供|填写|输入|告知|发送).{0,15}(验证码|密码|身份证|银行卡|个人信息|账号)"],
    "提供信息": [r"(提供了|告知了|发送了|输入了|填写了).{0,15}(验证码|密码|身份证|银行卡|个人信息|账号)", r"将.{0,12}(验证码|密码|身份证号|银行卡号).{0,12}(告诉|发给|提供)"],
    "要求共享屏幕": [r"(要求|让|叫).{0,15}(共享屏幕|屏幕共享|投屏|远程控制|远程协助)", r"开启.{0,8}(共享屏幕|远程控制)"],
    "共享屏幕": [r"(进行|开启|打开|通过).{0,10}(共享屏幕|屏幕共享|投屏|远程控制|远程协助)", r"共享了屏幕", r"乐播投屏"],
    "承诺利益": [r"承诺", r"收益", r"回报", r"返利", r"返现", r"佣金", r"补偿", r"免费.{0,8}(领取|赠送)", r"奖励"],
    "要求转账": [r"(要求|让|叫|指示|需).{0,15}(转账|汇款|打款)", r"按照.{0,10}(指示|要求).{0,12}(转账|汇款)"],
    "转账给": [r"转账(至|给|到)", r"向.{0,20}转账", r"汇款(至|给|到)", r"打款(至|给|到)"],
    "转账金额": [r"(转账|汇款|打款|付款).{0,20}\d+(?:\.\d+)?\s*(?:元|万)", r"\d+(?:\.\d+)?\s*(?:元|万).{0,12}(转账|汇款|打款)"],
    "要求充值": [r"(要求|让|叫|需|必须).{0,15}(充值|充钱|入金)", r"继续充值", r"充值.{0,10}(才能|才可)"],
    "充值到": [r"充值了", r"充值到", r"充入", r"入金", r"在.{0,15}(平台|APP|账户).{0,10}充值"],
    "提现": [r"提现", r"取现", r"提取.{0,8}(现金|余额|资金)"],
    "返款": [r"返还.{0,12}(元|资金|款)", r"退回.{0,12}(元|资金|款)", r"退款.{0,12}(到账|成功)", r"返现.{0,12}\d", r"返款"],
    "无法提现": [r"无法提现", r"不能提现", r"提现失败", r"提现不了", r"不让提现", r"限制提现", r"无法取现"],
    "损失金额": [r"损失.{0,12}\d", r"被骗.{0,12}\d", r"共计.{0,8}(被骗|损失)", r"损失价值", r"损失人民币"],
    "使用平台": [r"使用.{0,15}(微信|QQ|支付宝|APP|平台|软件)", r"在.{0,15}(APP|平台|微信|QQ|支付宝).{0,12}(操作|聊天|转账|付款)", r"登录.{0,12}(APP|平台|软件)"],
    "发生时间": [r"20\d{2}年\d{1,2}月\d{1,3}日", r"\d{1,2}时\d{0,2}分", r"当天.{0,8}(上午|下午|晚上|凌晨)"],
    "发生地点": [r"在家中", r"在家里", r"位于", r"所在地", r"案发地", r"在.{0,12}(公司|学校|宿舍|办公室|店内|小区|村|镇|路)"],
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text(text: str) -> str:
    return re.sub(r"[\s\u3000]+", "", text).strip('"“”')


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_number(value: str) -> int:
    return int(hashlib.sha256(f"{SEED}:{value}".encode("utf-8")).hexdigest()[:16], 16)


def compile_patterns() -> dict[str, list[re.Pattern[str]]]:
    return {
        relation: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        for relation, patterns in RELATION_PATTERNS.items()
    }


def relation_hints(text: str, patterns: dict[str, list[re.Pattern[str]]]) -> tuple[set[str], dict[str, int]]:
    hints = set()
    hits = {}
    for relation, relation_patterns in patterns.items():
        count = sum(min(3, len(pattern.findall(text))) for pattern in relation_patterns)
        if count:
            hints.add(relation)
            hits[relation] = count
    return hints, hits


def train_relation_support(root: Path) -> Counter:
    return Counter(
        label
        for row in read_jsonl(root / "datasets" / "relation_v3" / "train.jsonl")
        for label in row["labels"]
    )


def make_candidates(
    items: list[dict[str, Any]],
    source_type: str,
    existing_hashes: set[str],
    existing_dialogue_ids: set[str],
    patterns: dict[str, list[re.Pattern[str]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidates = []
    duplicate_hashes = set()
    stats = Counter()
    for index, item in enumerate(items, 1):
        if source_type == "对话":
            source_key = str(item.get("id") or f"dialogue_source_{index:06d}")
            text = str(item.get("dialogue") or "").strip()
            category = str(item.get("fraud_category_standard") or "未分类")
            if source_key in existing_dialogue_ids:
                stats["excluded_existing_id"] += 1
                continue
        else:
            source_key = f"case_source_{index:06d}"
            text = str(item.get("案情描述") or item.get("text") or "").strip()
            category = str(item.get("案件类别") or "未分类")
        if not text:
            stats["excluded_empty"] += 1
            continue
        digest = text_hash(text)
        if digest in existing_hashes:
            stats["excluded_existing_text"] += 1
            continue
        if digest in duplicate_hashes:
            stats["excluded_duplicate_candidate"] += 1
            continue
        duplicate_hashes.add(digest)
        hints, hits = relation_hints(text, patterns)
        candidates.append({
            "source_key": source_key,
            "source_index": index,
            "source_type": source_type,
            "category": category,
            "text": text,
            "text_hash": digest,
            "hints": hints,
            "hits": hits,
            "item": item,
        })
    stats["available"] = len(candidates)
    return candidates, dict(stats)


def select_candidates(
    candidates: list[dict[str, Any]],
    count: int,
    train_support: Counter,
) -> list[dict[str, Any]]:
    if len(candidates) < count:
        raise ValueError(f"可选数据只有{len(candidates)}篇，少于目标{count}篇")
    max_support = max(train_support.values())
    relation_weights = {
        relation: min(8.0, math.sqrt((max_support + 1) / (train_support[relation] + 1)))
        for relation in RELATION_PATTERNS
    }
    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    coverage = Counter()
    category_counts = Counter()
    available_categories = sorted({candidate["category"] for candidate in candidates})

    def score(candidate: dict[str, Any]) -> tuple[float, int, int, int]:
        relation_score = 0.0
        for relation in candidate["hints"]:
            deficit = max(0, RELATION_HINT_TARGET - coverage[relation])
            relation_score += relation_weights[relation] * (1.0 + deficit / RELATION_HINT_TARGET)
            relation_score += min(3, candidate["hits"].get(relation, 0)) * 0.08
        category_deficit = max(0, CATEGORY_MINIMUM - category_counts[candidate["category"]])
        category_score = category_deficit * 0.25
        return (
            relation_score + category_score,
            len(candidate["hints"]),
            min(len(candidate["text"]), 3000),
            -stable_number(candidate["source_key"]),
        )

    def add(candidate: dict[str, Any]) -> None:
        selected.append(candidate)
        selected_keys.add(candidate["source_key"])
        coverage.update(candidate["hints"])
        category_counts[candidate["category"]] += 1

    # 先计算最大程度均衡的类别配额。样本不足的类别全部保留，其余名额均摊。
    capacities = Counter(candidate["category"] for candidate in candidates)
    category_targets = Counter()
    for _ in range(count):
        available = [category for category in available_categories if category_targets[category] < capacities[category]]
        category = min(available, key=lambda value: (category_targets[value], value))
        category_targets[category] += 1

    # 从可用量最少的类别开始，在各类别内部按关系缺口和长尾权重选择。
    for category in sorted(available_categories, key=lambda value: (capacities[value], value)):
        while category_counts[category] < category_targets[category]:
            pool = [
                candidate for candidate in candidates
                if candidate["category"] == category and candidate["source_key"] not in selected_keys
            ]
            if not pool:
                break
            add(max(pool, key=score))

    # 理论上类别配额已经填满；保留兜底逻辑处理异常数据。
    while len(selected) < count:
        pool = [candidate for candidate in candidates if candidate["source_key"] not in selected_keys]
        add(max(pool, key=score))
    return selected


def distribution(selected: list[dict[str, Any]]) -> dict[str, Any]:
    relations = Counter()
    relation_hits = Counter()
    for candidate in selected:
        relations.update(candidate["hints"])
        relation_hits.update(candidate["hits"])
    return {
        "documents": len(selected),
        "fraud_categories": dict(sorted(Counter(candidate["category"] for candidate in selected).items())),
        "relation_hint_documents": {
            relation: relations[relation] for relation in RELATION_PATTERNS
        },
        "relation_hint_hits": {
            relation: relation_hits[relation] for relation in RELATION_PATTERNS
        },
        "documents_without_relation_hint": sum(not candidate["hints"] for candidate in selected),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="为V3定向筛选关系覆盖全面的新样本")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--per-source", type=int, default=DEFAULT_PER_SOURCE)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output_dir = (args.output_dir or root / "datas" / "v3_candidates").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    dialogue_source = root / "datas" / "code" / "对话数据集.json"
    case_source = root / "datas" / "code" / "诈骗案例数据集_重分类.json"
    current_dialogue = root / "datas" / "test" / "对话测试.json"
    current_case = root / "datas" / "test" / "案例测试.json"
    dialogue_data = read_json(dialogue_source)
    all_dialogues = dialogue_data.get("items", dialogue_data)
    all_cases = read_json(case_source)
    if isinstance(all_cases, dict):
        all_cases = all_cases.get("items", [])
    current_dialogue_data = read_json(current_dialogue)
    current_dialogues = current_dialogue_data.get("items", current_dialogue_data)
    current_cases = read_json(current_case)
    if isinstance(current_cases, dict):
        current_cases = current_cases.get("items", [])

    existing_hashes = {
        text_hash(str(item.get("dialogue") or "")) for item in current_dialogues
    } | {
        text_hash(str(item.get("案情描述") or item.get("text") or "")) for item in current_cases
    }
    existing_dialogue_ids = {str(item.get("id")) for item in current_dialogues if item.get("id")}
    patterns = compile_patterns()
    support = train_relation_support(root)
    dialogue_candidates, dialogue_exclusions = make_candidates(
        all_dialogues, "对话", existing_hashes, existing_dialogue_ids, patterns
    )
    case_candidates, case_exclusions = make_candidates(
        all_cases, "案例", existing_hashes, existing_dialogue_ids, patterns
    )
    selected_dialogues = select_candidates(dialogue_candidates, args.per_source, support)
    selected_cases = select_candidates(case_candidates, args.per_source, support)

    dialogue_items = [candidate["item"] for candidate in selected_dialogues]
    case_items = []
    for index, candidate in enumerate(selected_cases, 1):
        item = dict(candidate["item"])
        item["id"] = f"case_v3_{index:06d}"
        item["source_index"] = candidate["source_index"]
        case_items.append(item)
    dialogue_output = {
        "standard_categories": dialogue_data.get("standard_categories", []),
        "items": dialogue_items,
    }
    write_json(output_dir / f"对话候选{args.per_source}.json", dialogue_output)
    write_json(output_dir / f"案例候选{args.per_source}.json", case_items)

    combined_hashes = [candidate["text_hash"] for candidate in selected_dialogues + selected_cases]
    if len(combined_hashes) != len(set(combined_hashes)):
        raise ValueError("V3候选内部存在重复文本")
    if set(combined_hashes) & existing_hashes:
        raise ValueError("V3候选与现有600篇存在重复文本")
    if len(dialogue_items) != args.per_source or len(case_items) != args.per_source:
        raise ValueError("输出数量不满足要求")

    available_relation_counts = {
        "对话": {
            relation: sum(relation in candidate["hints"] for candidate in dialogue_candidates)
            for relation in RELATION_PATTERNS
        },
        "案例": {
            relation: sum(relation in candidate["hints"] for candidate in case_candidates)
            for relation in RELATION_PATTERNS
        },
    }
    manifest = {
        "version": "v3_candidates_1.0",
        "seed": SEED,
        "selection_purpose": "覆盖28种关系线索、诈骗类别和对话/案例两种来源",
        "important_note": "关系覆盖为关键词启发式估计，不是金标；必须经过LLM抽取和人工审核",
        "counts": {
            "dialogue": len(dialogue_items),
            "case": len(case_items),
            "total": len(dialogue_items) + len(case_items),
        },
        "parameters": {
            "per_source": args.per_source,
            "relation_hint_target_per_source": RELATION_HINT_TARGET,
            "category_minimum_per_source_when_available": CATEGORY_MINIMUM,
        },
        "excluded_existing_documents": 600,
        "candidate_exclusion_statistics": {
            "dialogue": dialogue_exclusions,
            "case": case_exclusions,
        },
        "current_train_relation_support": {
            relation: support[relation] for relation in RELATION_PATTERNS
        },
        "available_relation_hint_documents": available_relation_counts,
        "selected_distribution": {
            "dialogue": distribution(selected_dialogues),
            "case": distribution(selected_cases),
            "combined": distribution(selected_dialogues + selected_cases),
        },
        "selected_dialogue_ids": [candidate["source_key"] for candidate in selected_dialogues],
        "selected_case_source_indexes": [candidate["source_index"] for candidate in selected_cases],
        "selected_case_ids": [item["id"] for item in case_items],
        "source_sha256": {
            "datas/code/对话数据集.json": file_sha256(dialogue_source),
            "datas/code/诈骗案例数据集_重分类.json": file_sha256(case_source),
            "datas/test/对话测试.json": file_sha256(current_dialogue),
            "datas/test/案例测试.json": file_sha256(current_case),
        },
    }
    write_json(output_dir / "选择报告.json", manifest)
    (output_dir / "README.md").write_text(
        "# V3定向扩充候选集\n\n"
        f"本目录包含{args.per_source}篇新对话和{args.per_source}篇新案例，共{args.per_source * 2}篇。"
        "已排除V2所用600篇及文本重复项。筛选同时考虑28种关系关键词、诈骗类别和来源类型。\n\n"
        "这些文件只是候选原文，不是金标；`选择报告.json`中的关系覆盖也是关键词启发式估计。"
        "下一步应单独调用LLM抽取，并完成人工审核后再合并为V3。\n\n"
        "```powershell\n"
        "python .\\code\\llmuse.py `\n"
        f"  --dialogue \".\\datas\\v3_candidates\\对话候选{args.per_source}.json\" `\n"
        f"  --case \".\\datas\\v3_candidates\\案例候选{args.per_source}.json\" `\n"
        "  --output \".\\outputs\\llm_v3_candidates\"\n"
        "```\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output_dir": str(output_dir),
        "counts": manifest["counts"],
        "combined_relation_hint_documents": manifest["selected_distribution"]["combined"]["relation_hint_documents"],
        "combined_fraud_categories": manifest["selected_distribution"]["combined"]["fraud_categories"],
        "documents_without_relation_hint": manifest["selected_distribution"]["combined"]["documents_without_relation_hint"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
