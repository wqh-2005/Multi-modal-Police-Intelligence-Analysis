"""review 别名组核验与官方化合并（规则 + 官方应用库定向核验）。

背景：审计把 1,434 组「仅差 APP/软件/客户端/平台 后缀」的应用程序组降级为 review。
本脚本按官方命名习惯收敛这批组：

  1. 去掉 `APP/软件/客户端/平台` 后缀后基础名一致 → 同一产品，可合并到基础名；
  2. 基础名泛化、带截断/标点异常、或组内基础名不一致 → 记为 dispute，交人工确认；
  3. 只对 dispute（及可选抽样）去官方来源核验，避免对 1,400+ 组全量请求：
       - 应用宝（腾讯官方应用商店，sj.qq.com）搜索应用名与包名；
       - 必应国内版（cn.bing.com，RSS 输出，精确检索）搜索品牌官网；
  4. 核验结果作为证据：官方应用同名 / 官方站点命中 → 可合并；
     都找不到 → 保持 dispute，交人工确认。

产物（默认 experiments/KEO/data/person_a/name_cleaning/review_resolution/）：
  official_cache.json          官方检索缓存（应用宝 + 必应，按基础名，可复用）
  review_alias_decisions.json  逐组决策（merge / dispute）与官方证据
  review_alias_merge.json      仅 merge 组的 (类型, 原名)→规范名 映射
  review_alias_disputes.md     有异议清单（交人工确认）
  review_alias_summary.json    汇总

用法：
  python -m experiments.KEO.core.data.resolve_review_aliases                    # 只走规则，不联网
  python -m experiments.KEO.core.data.resolve_review_aliases --verify disputes  # 只核验有异议项
  python -m experiments.KEO.core.data.resolve_review_aliases --verify disputes,sample --sample-size 20
"""
from __future__ import annotations

import argparse
import html
import json
import random
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import NAME_CLEANING_DIR, rel

DEFAULT_CANDIDATES = NAME_CLEANING_DIR / "entity_alias_candidates.json"
DEFAULT_OUTPUT = NAME_CLEANING_DIR / "review_resolution"
DEFAULT_MANUAL = DEFAULT_OUTPUT / "review_alias_manual.json"

SUFFIX_RE = re.compile(r"(app|软件|客户端|平台)$", re.I)
GENERIC_BASES = {
    "app", "软件", "客户端", "平台", "会议", "视频会议", "网上", "网站", "网页", "网络",
    "链接", "网址", "群", "群聊", "聊天", "聊天软件", "交友软件", "游戏", "投资", "理财",
    "贷款", "借款", "银行", "证券", "保险", "购物", "直播", "短视频", "浏览器", "输入法",
    "系统", "工具", "助手", "小程序", "公众号", "官网", "商城", "钱包", "支付",
}
SHORT_BRANDS = {"qq", "yy", "soul", "zoom", "icq", "blued", "boss", "steam", "teams", "skype", "gmail", "uc", "sofi", "udao", "dst", "58", "360", "aw"}
COMPANY_TAILS = ("科技", "传媒", "网络", "信息技术", "技术", "集团", "服务", "数据", "电子商务", "文化", "教育")
TRUNCATED_TAILS = ("手机", "银行", "官网", "网页版", "电脑版", "下载", "网址", "客户端")
BAD_CHARS = set("+＋.。·、,，")
MARKETING_CUTS = ("-", "—", "–", "（", "(", "|", ":", "：", "_", " ")
# 必应结果里不算“官方站点”的域名
NON_OFFICIAL_DOMAINS = (
    "baike.baidu.com", "zhidao.baidu.com", "zhihu.com", "baijiahao.baidu.com", "sohu.com",
    "sina.com", "sina.cn", "163.com", "qq.com", "toutiao.com", "csdn.net", "jianshu.com",
    "douban.com", "bilibili.com", "taobao.com", "tmall.com", "jd.com", "pinduoduo.com",
    "xiaohongshu.com", "weibo.com", "apps.apple.com", "sj.qq.com", "appgallery.huawei.com",
    "app.mi.com", "wandoujia.com", "coolapk.com", "onlinedown.net", "cr173.com", "pcsoft.com.cn",
    "zol.com.cn", "downxia.com", "duote.com", "pc6.com", "mydown.com", "qqtn.com", "ddooo.com",
)


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def base_of(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).strip()
    text = re.sub(r"\s+", " ", text)
    return SUFFIX_RE.sub("", normalize(text)).strip()


def official_short_name(track_name: str) -> str:
    text = unicodedata.normalize("NFKC", track_name or "")
    cut = len(text)
    for marker in MARKETING_CUTS:
        index = text.find(marker)
        if index > 0:
            cut = min(cut, index)
    return text[:cut].strip()


def choose_display(members: list[dict[str, Any]], base: str) -> str:
    """优先取语料中不带通用后缀、提及最高的写法，再去掉可能残留的后缀。"""
    plain = [
        m for m in members
        if base_of(m["name"]) == base and SUFFIX_RE.sub("", normalize(m["name"])).strip() == normalize(m["name"])
    ]
    pool = plain or [m for m in members if base_of(m["name"]) == base] or members
    best = sorted(pool, key=lambda m: (-m["mentions"], m["name"]))[0]["name"]
    stripped = SUFFIX_RE.sub("", unicodedata.normalize("NFKC", best).strip()).strip()
    return stripped or best


def rule_classify(group: dict[str, Any]) -> tuple[str, str, str, str]:
    """不联网的规则判定，返回 (action, reason_code, reason, base)。"""
    members = group["members"]
    bases = {base_of(m["name"]) for m in members}
    if len(bases) > 1:
        return "dispute", "different_base", "组内去掉后缀后基础名不一致", ""
    base = bases.pop()
    if not base:
        return "dispute", "empty_base", "基础名为空", base
    if base in GENERIC_BASES:
        return "dispute", "generic_base", f"基础名“{base}”是泛化词，不能当共享概念", base
    if set(base) & BAD_CHARS:
        return "dispute", "punctuation", f"基础名“{base}”含分隔/标点，疑似拼写变体", base
    tail = next((t for t in TRUNCATED_TAILS if base.endswith(t) and base != t), None)
    if tail:
        return "dispute", "truncated", f"基础名以“{tail}”结尾，疑似截断（官方名可能更长）", base
    company_tail = next((t for t in COMPANY_TAILS if base.endswith(t)), None)
    if company_tail:
        return "merge", "company_like", f"仅差通用后缀；基础名以“{company_tail}”结尾，按公司/品牌名合并", base
    if len(base) <= 2 and base not in SHORT_BRANDS:
        return "merge", "short_base", "仅差通用后缀；基础名为短品牌名，按同一产品合并", base
    return "merge", "suffix_only", "仅差通用后缀，视为同一产品", base


def load_cache(path: Path) -> dict[str, Any]:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def fetch(url: str, timeout: float, retries: int) -> tuple[str | None, str | None]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    last_error: str | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace"), None
        except Exception as error:  # noqa: BLE001
            last_error = f"{type(error).__name__}: {error}"
            time.sleep(1.5 * (attempt + 1))
    return None, last_error


APPSTORE_ITEM_RE = re.compile(
    r'"pkg_name":"(?P<pkg>[^"]+)","app_id":"?(?P<app_id>\d+)"?,"name":"(?P<name>[^"]+)"'
)


def search_appstore(name: str, timeout: float, retries: int) -> tuple[list[dict[str, Any]], str | None]:
    """腾讯应用宝（官方应用商店）搜索。"""
    url = "https://sj.qq.com/search?" + urllib.parse.urlencode({"q": name})
    page, error = fetch(url, timeout, retries)
    if page is None:
        return [], error
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in APPSTORE_ITEM_RE.finditer(page):
        pkg = match.group("pkg")
        if pkg in seen:
            continue
        seen.add(pkg)
        results.append(
            {
                "name": html.unescape(match.group("name")),
                "pkg_name": pkg,
                "app_id": match.group("app_id"),
            }
        )
        if len(results) >= 8:
            break
    return results, None


BING_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)


def search_bing(name: str, timeout: float, retries: int) -> tuple[list[dict[str, Any]], str | None]:
    """必应国内版精确检索（RSS 输出）：<名> 官网。"""
    query = f'"{name}" 官网'
    url = "https://cn.bing.com/search?" + urllib.parse.urlencode({"q": query, "format": "rss", "setlang": "zh-Hans"})
    page, error = fetch(url, timeout, retries)
    if page is None:
        return [], error
    results: list[dict[str, Any]] = []
    for block in BING_ITEM_RE.findall(page):
        title_match = re.search(r"<title>(.*?)</title>", block, re.S)
        link_match = re.search(r"<link>(.*?)</link>", block, re.S)
        desc_match = re.search(r"<description>(.*?)</description>", block, re.S)
        if not title_match or not link_match:
            continue
        link = html.unescape(link_match.group(1)).strip()
        results.append(
            {
                "title": html.unescape(title_match.group(1)).strip(),
                "link": link,
                "description": html.unescape(desc_match.group(1)).strip() if desc_match else "",
            }
        )
        if len(results) >= 8:
            break
    return results, None


def domain_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""


def is_official_site(base: str, item: dict[str, Any]) -> bool:
    title = normalize(item.get("title", ""))
    domain = domain_of(item.get("link", ""))
    if not domain or any(domain.endswith(bad) for bad in NON_OFFICIAL_DOMAINS):
        return False
    if base not in title:
        return False
    return any(marker in item.get("title", "") for marker in ("官网", "官方", "官方网站", "下载")) or base in domain


def related_name(base: str, other: str) -> bool:
    short = normalize(official_short_name(other))
    if not short:
        return False
    if short == base:
        return True
    if short in base or base in short:
        return abs(len(short) - len(base)) <= 4
    return False


def verify_with_official(
    base: str, appstore: list[dict[str, Any]], bing: list[dict[str, Any]]
) -> tuple[str | None, str, str, list[str]]:
    """返回 (升级后的 action 或 None, reason_code, 说明, 证据列表)。"""
    evidence: list[str] = []
    exact = [item for item in appstore if normalize(item["name"]) == base]
    if exact:
        sellers = {item["pkg_name"].split(".")[1] if "." in item["pkg_name"] else item["pkg_name"] for item in exact}
        evidence.append(f"应用宝同名应用：{exact[0]['name']}（{exact[0]['pkg_name']}）")
        if len(sellers) > 1:
            return "dispute", "multi_official", f"应用宝有 {len(sellers)} 个不同包名的同名应用", evidence
        return "merge", "official_app", "应用宝存在同名官方应用", evidence

    official_sites = [item for item in bing if is_official_site(base, item)]
    for item in official_sites[:2]:
        evidence.append(f"官方站点：{item['title'][:40]}（{item['link'][:60]}）")
    if official_sites:
        return "merge", "official_site", "必应检索到品牌官方站点", evidence

    related = [item for item in appstore if related_name(base, item["name"])]
    if related:
        evidence.append(f"应用宝同族应用：{related[0]['name']}（{related[0]['pkg_name']}）")
        return "merge", "official_app_related", "应用宝存在同族应用", evidence

    bing_hits = [item for item in bing if base in normalize(item.get("title", ""))]
    for item in bing_hits[:2]:
        evidence.append(f"必应结果：{item['title'][:40]}（{domain_of(item['link'])}）")
    if bing_hits:
        return "dispute", "bing_related_only", "必应只有关联结果，无官方站点，需人工确认", evidence
    return "dispute", "no_official_match", "应用宝与必应均未找到官方应用或官网", evidence


def load_manual(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["group_id"]): item for item in payload.get("groups", [])}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="核验并官方化 review 别名组")
    parser.add_argument("--candidates", default=str(DEFAULT_CANDIDATES), help="entity_alias_candidates.json")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出目录")
    parser.add_argument("--manual", default=str(DEFAULT_MANUAL), help="人工确认的组级决策 JSON")
    parser.add_argument("--verify", default="", help="官方核验范围：disputes / sample / all；留空则不联网")
    parser.add_argument("--sample-size", type=int, default=30, help="抽样核验的 merge 组数量")
    parser.add_argument("--sample-seed", type=int, default=42, help="抽样种子")
    parser.add_argument("--delay", type=float, default=1.2, help="单次请求间隔秒数（官方库限流）")
    parser.add_argument("--timeout", type=float, default=10.0, help="单次请求超时秒数")
    parser.add_argument("--retries", type=int, default=2, help="失败重试次数")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新检索")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    candidates_path = Path(args.candidates)
    output_dir = Path(args.output).expanduser()
    if not candidates_path.is_file():
        raise SystemExit(f"输入不存在: {candidates_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    review_groups = [g for g in payload["groups"] if g.get("recommended_action") == "review"]
    if not review_groups:
        raise SystemExit("没有 review 组")

    decisions: list[dict[str, Any]] = []
    for group in review_groups:
        action, reason_code, reason, base = rule_classify(group)
        decisions.append(
            {
                "group_id": group["group_id"],
                "entity_type": group["entity_type"],
                "base": base,
                "action": action,
                "reason_code": reason_code,
                "reason": reason,
                "canonical": choose_display(group["members"], base) if base else group["canonical_candidate"],
                "member_mentions": group["member_mentions"],
                "members": group["members"],
                "official": [],
            }
        )

    disputes = [d for d in decisions if d["action"] == "dispute"]
    merges = [d for d in decisions if d["action"] == "merge"]
    scope = {part.strip() for part in args.verify.split(",") if part.strip()}
    targets: list[dict[str, Any]] = []
    if "all" in scope:
        targets = decisions
    else:
        if "disputes" in scope:
            targets.extend(disputes)
        if "sample" in scope:
            rng = random.Random(args.sample_seed)
            candidates = [d for d in merges if d not in targets]
            targets.extend(rng.sample(candidates, min(args.sample_size, len(candidates))))
    targets = [d for d in targets if d["base"]]
    print(f"review 组 {len(decisions)}：规则可合并 {len(merges)}、有异议 {len(disputes)}；本次官方核验 {len(targets)} 组")

    cache_path = output_dir / "official_cache.json"
    cache = {} if args.refresh else load_cache(cache_path)
    failures = 0
    for index, record in enumerate(targets, 1):
        base = record["base"]
        if base not in cache:
            appstore, error_app = search_appstore(base, args.timeout, args.retries)
            time.sleep(args.delay)
            bing, error_bing = search_bing(base, args.timeout, args.retries)
            if error_app or error_bing:
                failures += 1
                print(f"  ! {base}: appstore={error_app} bing={error_bing}")
            cache[base] = {
                "queried_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "appstore": appstore,
                "bing": bing,
                "error": {"appstore": error_app, "bing": error_bing},
            }
            if index % 10 == 0 or index == len(targets):
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            time.sleep(args.delay)

        entry = cache[base]
        appstore = entry.get("appstore", [])
        bing = entry.get("bing", [])
        upgraded, reason_code, reason, evidence = verify_with_official(base, appstore, bing)
        record.update(
            reason_code=reason_code,
            reason=reason,
            official=appstore[:3],
            bing=bing[:3],
            evidence=evidence,
            verified=True,
        )
        # 官方核验结果可上调（dispute → merge）或下调（merge → dispute）
        record["rule_action"] = record["action"]
        if upgraded:
            record["action"] = upgraded
    if targets:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    manual = load_manual(Path(args.manual))
    for record in decisions:
        override = manual.get(record["group_id"])
        if not override:
            continue
        record["rule_action"] = record.get("rule_action") or record["action"]
        record["action"] = override.get("action", record["action"])
        record["canonical"] = override.get("canonical", record["canonical"])
        record["reason_code"] = "manual_confirmed"
        record["reason"] = override.get("reason", "人工确认")
        record["manual"] = True

    merges = [d for d in decisions if d["action"] == "merge"]
    disputes = [d for d in decisions if d["action"] == "dispute"]
    kept = [d for d in decisions if d["action"] == "keep"]
    merge_map: dict[tuple[str, str], str] = {}
    for record in merges:
        for member in record["members"]:
            if member["name"] != record["canonical"]:
                merge_map[(record["entity_type"], member["name"])] = record["canonical"]

    decisions.sort(key=lambda r: (r["action"] != "dispute", -r["member_mentions"], r["group_id"]))
    by_reason = Counter((d["action"], d["reason_code"]) for d in decisions)
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_candidates": rel(candidates_path),
        "official_source": (
            "腾讯应用宝（sj.qq.com）搜索应用名 + 必应国内版（cn.bing.com RSS，精确检索“名 官网”）"
            if targets
            else None
        ),
        "verified_groups": sum(1 for d in decisions if d.get("verified")),
        "verify_failures": failures,
        "review_groups": len(decisions),
        "merge_groups": len(merges),
        "dispute_groups": len(disputes),
        "keep_groups": len(kept),
        "manual_confirmed_groups": sum(1 for d in decisions if d.get("manual")),
        "merge_mention_total": sum(d["member_mentions"] for d in merges),
        "dispute_mention_total": sum(d["member_mentions"] for d in disputes),
        "merge_mapping_pairs": len(merge_map),
        "by_reason": {f"{k[0]}|{k[1]}": v for k, v in sorted(by_reason.items())},
    }

    (output_dir / "review_alias_decisions.json").write_text(
        json.dumps({"summary": summary, "decisions": decisions}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    (output_dir / "review_alias_merge.json").write_text(
        json.dumps(
            {
                "version": "review-1.0",
                "generated_at": summary["generated_at"],
                "official_source": summary["official_source"],
                "mapping_count": len(merge_map),
                "mappings": [
                    {"entity_type": k[0], "original": k[1], "canonical": v}
                    for k, v in sorted(merge_map.items())
                ],
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "review_alias_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = ["# review 别名组核验（官方化合并）", ""]
    lines.append(f"> 生成时间：{summary['generated_at']}  ")
    lines.append(f"> 官方来源：{summary['official_source'] or '本次未联网（仅规则）'}  ")
    lines.append(
        f"> review 组 {summary['review_groups']:,}：可合并 {summary['merge_groups']:,}、有异议 {summary['dispute_groups']:,}"
        f"；本次核验 {summary['verified_groups']:,} 组，失败 {summary['verify_failures']}"
    )
    lines.append("")
    lines.append("## 1. 判定口径")
    lines.append("")
    lines.append("1. 去掉 `APP/软件/客户端/平台` 后缀后基础名一致 → 视为同一产品；")
    lines.append("2. 泛化基础名、过短名、带标点/分隔、疑似截断（如“XX手机”）、疑似公司名（如“XX科技”）→ 有异议；")
    lines.append("3. 有异议项再用官方应用库核验：同名 → 可合并（official_verified）；同族 → 可合并（official_related）；")
    lines.append("   对不上、无结果、多开发者同名 → 保持有异议，交人工确认。")
    lines.append("")
    lines.append("## 2. 分布")
    lines.append("")
    lines.append("| 结论 | 原因 | 组数 |")
    lines.append("|---|---|---:|")
    for key, count in sorted(summary["by_reason"].items()):
        action, reason = key.split("|")
        lines.append(f"| {action} | {reason} | {count:,} |")
    lines.append("")
    if disputes:
        lines.append("## 3. 有异议清单（待确认：合并 / 不合并）")
        lines.append("")
        lines.append("| # | group_id | 组内写法（提及） | 规则建议 | 核验结论 | 官方证据 |")
        lines.append("|---:|---|---|---|---|---|")
        for index, record in enumerate(disputes, 1):
            names = " / ".join(f"{m['name']}:{m['mentions']}" for m in record["members"])
            official = "；".join(record.get("evidence") or []) or "-"
            lines.append(
                f"| {index} | `{record['group_id']}` | {names} | {record['canonical']} | "
                f"{record['reason_code']}：{record['reason']} | {official} |"
            )
        lines.append("")
    (output_dir / "review_alias_disputes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
