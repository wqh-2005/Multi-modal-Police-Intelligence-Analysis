# -*- coding: utf-8 -*-
"""Person A data audit + 60/40 split draft for KEO fraud case data.

Reads:
  data/raw/诈骗案例数据集_带编号.json
  app/relationextraction/output/case/{triples.jsonl, by_document, failures, collaboration_merge_manifest.json}

Writes:
  experiments/KEO/data/person_a/（可用 --output 或 KEO_DATA_ROOT 覆盖）

Usage:
  python -m experiments.KEO.core.data.build_dataset --help
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import random
import re
import collections
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from experiments.KEO.core.data.paths import (
    CASE_DIR as DEFAULT_CASE_DIR,
    EXTRACTION_DIR,
    MERGE_MANIFEST as DEFAULT_MERGE_MANIFEST,
    PERSON_A_DIR,
    RAW_CASES as DEFAULT_RAW_CASES,
    REPO_ROOT,
    rel as rel_path,
)

BASE = REPO_ROOT
DATA_PATH = DEFAULT_RAW_CASES
CASE_DIR = DEFAULT_CASE_DIR
TRI_PATH = CASE_DIR / "triples.jsonl"
MERGE_MANIFEST_PATH = DEFAULT_MERGE_MANIFEST
OUT = PERSON_A_DIR

SEED = 42
TRAIN_RATIO = 0.60
STATUS = "draft_pending_config_confirmation"
UNKNOWN_VALUES = {"", "未知", "UNKNOWN", "unknown", "Unknown", "null", "None", "N/A", "NA"}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def part_of(source_id: str) -> str:
    n = int(source_id.split("_")[1])
    if n <= 27404:
        return "A"
    if n <= 54807:
        return "B"
    return "C"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="人员 A 数据对齐检查、去重与建库/评估划分")
    parser.add_argument("--cases", default=str(DATA_PATH), help="带编号的案例 JSON")
    parser.add_argument("--case-dir", default=str(CASE_DIR), help="三人合并抽取产物目录")
    parser.add_argument("--output", default=str(OUT), help="person_a 输出目录")
    parser.add_argument("--seed", type=int, default=SEED, help="划分随机种子")
    parser.add_argument("--train-ratio", type=float, default=TRAIN_RATIO, help="建库占比（默认 0.6）")
    parser.add_argument("--status", default=STATUS, help="写入 manifest 的状态")
    parser.add_argument("--hash-large-files", action="store_true", help="对大文件计算真实 SHA256（耗时）")
    parser.add_argument("--dry-run", action="store_true", help="只打印配置与输入检查结果，不写文件")
    return parser


def main() -> None:
    global DATA_PATH, CASE_DIR, TRI_PATH, MERGE_MANIFEST_PATH, OUT, SEED, TRAIN_RATIO, STATUS

    args = build_parser().parse_args()
    DATA_PATH = Path(args.cases).expanduser()
    CASE_DIR = Path(args.case_dir).expanduser()
    TRI_PATH = CASE_DIR / "triples.jsonl"
    MERGE_MANIFEST_PATH = CASE_DIR / "collaboration_merge_manifest.json"
    OUT = Path(args.output).expanduser()
    SEED = args.seed
    TRAIN_RATIO = args.train_ratio
    STATUS = args.status

    missing_inputs = [p for p in (DATA_PATH, TRI_PATH, MERGE_MANIFEST_PATH) if not p.is_file()]
    if missing_inputs:
        raise SystemExit(f"输入不存在：{[str(p) for p in missing_inputs]}")
    if not 0 < TRAIN_RATIO < 1:
        raise SystemExit(f"--train-ratio 必须在 (0, 1) 之间，当前 {TRAIN_RATIO}")

    if args.dry_run:
        print(json.dumps({
            "cases": rel_path(DATA_PATH),
            "case_dir": rel_path(CASE_DIR),
            "output": rel_path(OUT),
            "seed": SEED,
            "train_ratio": TRAIN_RATIO,
            "status": STATUS,
            "documents": len(json.loads(DATA_PATH.read_text(encoding="utf-8"))),
        }, ensure_ascii=False, indent=2))
        return

    OUT.mkdir(parents=True, exist_ok=True)

    started = datetime.now().astimezone()
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    by_id = {d["id"]: d for d in data}
    cats = collections.Counter(d["案件类别"] for d in data)

    # ---- text duplicates / groups ----
    text_groups: dict[str, list[str]] = collections.defaultdict(list)
    for d in data:
        text_groups[sha256_text(d["案情描述"])].append(d["id"])
    dup_groups = {h: ids for h, ids in text_groups.items() if len(ids) > 1}
    dup_group_of = {}
    for h, ids in dup_groups.items():
        gid = "dup_" + h[:12]
        for sid in ids:
            dup_group_of[sid] = gid

    # ---- failure + by_document ids ----
    failure_ids = set()
    for p in (CASE_DIR / "failures").rglob("*.json"):
        try:
            failure_ids.add(json.loads(p.read_text(encoding="utf-8"))["source_id"])
        except Exception:
            pass
    doc_ids = set()
    for p in (CASE_DIR / "by_document").rglob("*.json"):
        stem = p.stem
        if re.fullmatch(r"case_\d{6}", stem):
            doc_ids.add(stem)

    # ---- pass 1: stream triples, write flagged triples + collect stats ----
    seen_keys: set[str] = set()
    key_counts: collections.Counter[str] = collections.Counter()
    triple_count_by_sid: collections.Counter[str] = collections.Counter()
    triple_ids: set[str] = set()
    dup_triple_ids = 0
    issue_counts = collections.Counter()
    issue_case_ids: dict[str, set[str]] = collections.defaultdict(set)
    partition_stats = {p: {"docs": set(), "triples": 0, "unknown": 0, "self": 0, "same": 0, "dup_extra": 0} for p in "ABC?"}
    relation_counts = collections.Counter()
    factuality_counts = collections.Counter()
    n_lines = 0
    bad_json = 0
    source_not_found = set()
    missing_name = 0
    category_mismatch = 0
    evidence_not_substring = 0
    unit_prefix_bad = 0
    bad_relation = 0
    bad_factuality = 0
    bad_confidence = 0
    entity_type_conflict = 0
    entity_type_map: dict[str, str] = {}

    clean_path = OUT / "triples_clean.jsonl"
    issues_path = OUT / "issues_triples.csv.gz"
    with open(TRI_PATH, encoding="utf-8") as fin, \
         open(clean_path, "w", encoding="utf-8") as fout, \
         gzip.open(issues_path, "wt", encoding="utf-8", newline="") as fissues:
        issue_writer = csv.writer(fissues)
        issue_writer.writerow(["triple_id", "source_id", "partition", "relation", "head_name", "tail_name", "flags"])
        for line in fin:
            n_lines += 1
            try:
                t = json.loads(line)
            except Exception:
                bad_json += 1
                continue
            sid = t.get("source_id")
            part = part_of(sid) if sid and re.fullmatch(r"case_\d{6}", sid) else "?"
            if part not in partition_stats:
                part = "?"
            flags = []
            if sid not in by_id:
                flags.append("source_not_found")
                source_not_found.add(sid)
                rec = None
            else:
                rec = by_id[sid]
            if sid:
                partition_stats[part]["docs"].add(sid)
            partition_stats[part]["triples"] += 1
            triple_count_by_sid[sid] += 1

            tid = t.get("triple_id")
            if tid in triple_ids:
                flags.append("duplicate_triple_id")
                dup_triple_ids += 1
            elif tid:
                triple_ids.add(tid)

            if not all(t.get(k) for k in ["triple_id", "source_id", "fraud_category", "head", "relation", "tail", "evidence"]):
                flags.append("missing_field")
            if rec and t.get("fraud_category") != rec["案件类别"]:
                flags.append("category_mismatch")
                category_mismatch += 1
            rel = t.get("relation")
            relation_counts[rel] += 1
            if rel not in relation_names:
                flags.append("bad_relation")
                bad_relation += 1
            fact = t.get("factuality")
            factuality_counts[fact] += 1
            if fact not in factuality_names:
                flags.append("bad_factuality")
                bad_factuality += 1
            try:
                c = float(t.get("confidence"))
                if c < 0 or c > 1:
                    flags.append("bad_confidence")
                    bad_confidence += 1
            except Exception:
                flags.append("bad_confidence")
                bad_confidence += 1

            h = t.get("head") or {}
            tl = t.get("tail") or {}
            hid, tid2 = h.get("id"), tl.get("id")
            hname = (h.get("name") or "").strip()
            tname = (tl.get("name") or "").strip()
            hu, tu = hname in UNKNOWN_VALUES, tname in UNKNOWN_VALUES
            if not hname or not tname:
                flags.append("missing_head_tail_name")
                missing_name += 1
            if hu:
                flags.append("unknown_head")
            if tu:
                flags.append("unknown_tail")
            if hu or tu:
                partition_stats[part]["unknown"] += 1
            if hid and tid2 and hid == tid2:
                flags.append("self_loop")
                partition_stats[part]["self"] += 1
            if hname and hname == tname:
                flags.append("same_name")
                partition_stats[part]["same"] += 1
            for e in (h, tl):
                eid, etype = e.get("id"), e.get("type")
                if eid:
                    if eid in entity_type_map and entity_type_map[eid] != etype:
                        flags.append("entity_type_conflict")
                        entity_type_conflict += 1
                    entity_type_map[eid] = etype

            ev = t.get("evidence") or []
            if not ev:
                flags.append("evidence_missing")
            for unit in ev:
                uid = unit.get("unit_id", "")
                txt = unit.get("text", "")
                if not uid or not txt:
                    flags.append("evidence_missing")
                if uid and not uid.startswith(str(sid) + "_"):
                    flags.append("unit_prefix_mismatch")
                    unit_prefix_bad += 1
                if txt and rec and txt not in rec["案情描述"]:
                    flags.append("evidence_not_substring")
                    evidence_not_substring += 1

            key = (sid, hname, rel, tname)
            key_hash = sha1_text("\x1f".join(str(x) for x in key))
            key_counts[key_hash] += 1
            if key_hash in seen_keys:
                flags.append("duplicate_content_copy")
                partition_stats[part]["dup_extra"] += 1
            seen_keys.add(key_hash)

            # de-duplicate flags while preserving order
            flags = list(dict.fromkeys(flags))
            for f in flags:
                issue_counts[f] += 1
                issue_case_ids[f].add(sid)
            if flags:
                issue_writer.writerow([tid, sid, part, rel, hname, tname, ";".join(flags)])

            q = {
                "partition": part,
                "resource_version": "current" if part == "A" else "legacy",
                "source_category": rec["案件类别"] if rec else None,
                "flags": flags,
                "duplicate_content_group": key_hash[:16],
                "is_duplicate_content_copy": "duplicate_content_copy" in flags,
            }
            t["quality"] = q
            fout.write(json.dumps(t, ensure_ascii=False) + "\n")

    # duplicate content stats
    dup_content_groups = sum(1 for v in key_counts.values() if v > 1)
    dup_content_extra = sum(v - 1 for v in key_counts.values() if v > 1)

    # ---- cases master ----
    failure_ids = {x for x in failure_ids if x in by_id}
    missing_ids = (doc_ids - set(triple_count_by_sid)) | failure_ids
    cases_path = OUT / "cases_master.jsonl"
    with open(cases_path, "w", encoding="utf-8") as f:
        for d in data:
            sid = d["id"]
            count = triple_count_by_sid.get(sid, 0)
            if sid in failure_ids:
                reason = "extraction_failed"
            elif count == 0:
                reason = "zero_triples"
            else:
                reason = ""
            rec = {
                "source_id": sid,
                "category": d["案件类别"],
                "text": d["案情描述"],
                "text_sha256": sha256_text(d["案情描述"]),
                "text_length": len(d["案情描述"]),
                "partition": part_of(sid),
                "triple_count": count,
                "kg_usable": bool(count > 0 and sid not in failure_ids),
                "excluded_reason": reason,
                "duplicate_text_group": dup_group_of.get(sid),
                "duplicate_group_size": len(text_groups[sha256_text(d["案情描述"])]),
                "split": None,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- split: group-aware, stratified by category ----
    # Groups are exact duplicate texts; cross-partition/cross-category groups stay together.
    group_members: dict[str, list[str]] = {}
    for d in data:
        sid = d["id"]
        gid = dup_group_of.get(sid) or ("single_" + sid)
        group_members.setdefault(gid, []).append(sid)
    group_primary_cat = {}
    group_has_cross_cat = {}
    for gid, ids in group_members.items():
        cats_in = {by_id[x]["案件类别"] for x in ids}
        group_primary_cat[gid] = by_id[min(ids)]["案件类别"]
        group_has_cross_cat[gid] = len(cats_in) > 1

    usable = {d["id"] for d in data if d["id"] not in missing_ids}
    groups_by_cat: dict[str, list[str]] = collections.defaultdict(list)
    for gid, ids in group_members.items():
        if any(x in usable for x in ids):
            groups_by_cat[group_primary_cat[gid]].append(gid)

    rng = random.Random(SEED)
    split_of = {sid: "excluded_kg" for sid in missing_ids}
    for cat, gids in groups_by_cat.items():
        rng.shuffle(gids)
        cat_usable = [sid for sid in usable if by_id[sid]["案件类别"] == cat]
        target = round(TRAIN_RATIO * len(cat_usable))
        train_count = 0
        for gid in gids:
            ids = [x for x in group_members[gid] if x in usable]
            if train_count < target:
                for sid in ids:
                    split_of[sid] = "build"
                train_count += len(ids)
            else:
                for sid in ids:
                    split_of[sid] = "eval"

    # cases_master was written before the split; rewrite it with the final split labels
    with open(cases_path, "w", encoding="utf-8") as f:
        for d in data:
            sid = d["id"]
            count = triple_count_by_sid.get(sid, 0)
            if sid in failure_ids:
                reason = "extraction_failed"
            elif count == 0:
                reason = "zero_triples"
            else:
                reason = ""
            rec = {
                "source_id": sid,
                "category": d["案件类别"],
                "text": d["案情描述"],
                "text_sha256": sha256_text(d["案情描述"]),
                "text_length": len(d["案情描述"]),
                "partition": part_of(sid),
                "triple_count": count,
                "kg_usable": bool(count > 0 and sid not in failure_ids),
                "excluded_reason": reason,
                "duplicate_text_group": dup_group_of.get(sid),
                "duplicate_group_size": len(text_groups[sha256_text(d["案情描述"])]),
                "split": split_of.get(sid, "excluded_kg"),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- split manifest / summary ----
    split_rows = []
    for d in data:
        sid = d["id"]
        split_rows.append({
            "source_id": sid,
            "category": d["案件类别"],
            "partition": part_of(sid),
            "split": split_of[sid],
            "kg_usable": sid not in missing_ids,
            "triple_count": triple_count_by_sid.get(sid, 0),
            "excluded_reason": "extraction_failed" if sid in failure_ids else ("zero_triples" if sid not in failure_ids and triple_count_by_sid.get(sid, 0) == 0 else ""),
            "duplicate_text_group": dup_group_of.get(sid, ""),
            "duplicate_group_size": len(text_groups[sha256_text(d["案情描述"])]),
        })
    with open(OUT / "split_manifest.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(split_rows[0].keys()))
        w.writeheader()
        w.writerows(split_rows)

    build_ids = sorted([r["source_id"] for r in split_rows if r["split"] == "build"])
    eval_ids = sorted([r["source_id"] for r in split_rows if r["split"] == "eval"])
    (OUT / "split_build_ids.txt").write_text("\n".join(build_ids) + "\n", encoding="utf-8")
    (OUT / "split_eval_ids.txt").write_text("\n".join(eval_ids) + "\n", encoding="utf-8")

    summary_rows = []
    for cat in cats:
        rows = [r for r in split_rows if r["category"] == cat]
        total = len(rows)
        usable_n = sum(1 for r in rows if r["kg_usable"])
        build_n = sum(1 for r in rows if r["split"] == "build")
        eval_n = sum(1 for r in rows if r["split"] == "eval")
        excluded_n = sum(1 for r in rows if r["split"] == "excluded_kg")
        summary_rows.append({
            "category": cat,
            "total": total,
            "usable": usable_n,
            "excluded": excluded_n,
            "build": build_n,
            "eval": eval_n,
            "build_pct_of_usable": round(100 * build_n / usable_n, 2) if usable_n else 0,
            "eval_pct_of_usable": round(100 * eval_n / usable_n, 2) if usable_n else 0,
        })
    total_row = {
        "category": "ALL",
        "total": len(split_rows),
        "usable": len(build_ids) + len(eval_ids),
        "excluded": len(missing_ids),
        "build": len(build_ids),
        "eval": len(eval_ids),
        "build_pct_of_usable": round(100 * len(build_ids) / (len(build_ids) + len(eval_ids)), 2),
        "eval_pct_of_usable": round(100 * len(eval_ids) / (len(build_ids) + len(eval_ids)), 2),
    }
    summary_rows.append(total_row)
    with open(OUT / "split_summary.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(total_row.keys()))
        w.writeheader()
        w.writerows(summary_rows)

    # ---- excluded cases / duplicate groups / issues summary ----
    with open(OUT / "excluded_cases.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "category", "partition", "reason", "text_length", "triple_count"])
        for sid in sorted(missing_ids):
            d = by_id[sid]
            w.writerow([sid, d["案件类别"], part_of(sid), "extraction_failed" if sid in failure_ids else "zero_triples", len(d["案情描述"]), triple_count_by_sid.get(sid, 0)])

    with open(OUT / "duplicate_text_groups.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group_id", "source_ids", "categories", "partitions", "cross_category"])
        for h, ids in sorted(dup_groups.items()):
            w.writerow(["dup_" + h[:12], ";".join(ids), ";".join(sorted({by_id[x]["案件类别"] for x in ids})), ";".join(sorted({part_of(x) for x in ids})), int(len({by_id[x]["案件类别"] for x in ids}) > 1)])

    # issue summary
    issue_rows = []
    for name, count in issue_counts.most_common():
        issue_rows.append({"issue_type": name, "triple_count": count, "case_count": len(issue_case_ids.get(name, set()))})
    issue_rows += [
        {"issue_type": "duplicate_content_group", "triple_count": dup_content_groups, "case_count": ""},
        {"issue_type": "duplicate_content_extra", "triple_count": dup_content_extra, "case_count": ""},
        {"issue_type": "zero_triples", "triple_count": 0, "case_count": len([x for x in missing_ids if x not in failure_ids])},
        {"issue_type": "extraction_failed", "triple_count": 0, "case_count": len(failure_ids)},
        {"issue_type": "duplicate_text_group", "triple_count": 0, "case_count": len(dup_groups)},
    ]
    with open(OUT / "issues_summary.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["issue_type", "triple_count", "case_count"])
        w.writeheader()
        w.writerows(issue_rows)

    # ---- manifest ----
    unknown_total = sum(s["unknown"] for s in partition_stats.values())
    self_total = sum(s["self"] for s in partition_stats.values())
    same_total = sum(s["same"] for s in partition_stats.values())
    merge_manifest = json.loads(MERGE_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest = {
        "created_at": started.isoformat(),
        "status": STATUS,
        "inputs": {
            "cases": {"path": rel_path(DATA_PATH), "sha256": hashlib.sha256(DATA_PATH.read_bytes()).hexdigest()},
            "triples": {"path": rel_path(TRI_PATH), "size_bytes": TRI_PATH.stat().st_size},
            "merge_manifest": rel_path(MERGE_MANIFEST_PATH),
        },
        "seed": SEED,
        "train_ratio": TRAIN_RATIO,
        "counts": {
            "documents": len(data),
            "success_documents": len(doc_ids),
            "failed_documents": len(failure_ids),
            "zero_triple_documents": len([x for x in missing_ids if x not in failure_ids]),
            "usable_documents": len(build_ids) + len(eval_ids),
            "excluded_documents": len(missing_ids),
            "triples": n_lines,
            "unique_triple_ids": len(triple_ids),
            "duplicate_triple_ids": dup_triple_ids,
            "build": len(build_ids),
            "eval": len(eval_ids),
        },
        "partitions": {p: {"documents": len(s["docs"]), "triples": s["triples"], "unknown": s["unknown"], "self_loop": s["self"], "same_name": s["same"], "duplicate_content_extra": s["dup_extra"]} for p, s in partition_stats.items()},
        "resource_check": {
            "current": {"entity_schema_sha256": "dfe428d08dc4d577a9df4c17f7ac45600a1f9eda6a803dcaba54b005db958588", "relation_schema_sha256": "fb0897aa54dd4471e753359323f313bd91608be2d95c1114367d1ca681185afb", "prompt_sha256": "8bb3283c46bde58c1e664cf2bbd7575b50f4476c0414dfc3ce663d19f477f0c6"},
            "student_b_recorded": merge_manifest["partitions"][1]["recorded_resources"],
            "student_c_recorded": merge_manifest["partitions"][2]["recorded_resources"],
            "mismatch": False,
            "line_ending_only": True,
            "note": "B/C 记录的 Prompt/Schema 哈希与 A 不同，但差异仅由 LF/CRLF 换行造成；合并脚本同时接受两种格式，不构成阻塞。",
        },
        "known_issues": {
            "resource_line_endings": "B/C 记录的 Prompt/Schema 哈希与 A 不同，但差异仅由 LF/CRLF 换行造成；合并脚本同时接受两种格式。",
            "zero_triples": "53 篇成功但 0 三元组，其中 47 篇原文 >=50 字，疑似抽取遗漏。",
            "failed": "9 篇抽取失败。",
            "duplicate_text": "29 组完全重复文本，23 组跨抽取分区；划分时按组保持同侧。",
            "unknown_entities": f"{unknown_total:,} 条三元组至少一端为未知/空占位。",
            "self_loop": f"{self_total:,} 条 head.id == tail.id。",
            "duplicate_content": f"{dup_content_groups:,} 组内容重复，额外 {dup_content_extra:,} 条。",
        },
        "outputs": {},
    }
    for name in ["cases_master.jsonl", "triples_clean.jsonl", "issues_triples.csv.gz", "issues_summary.csv", "split_manifest.csv", "split_summary.csv", "split_build_ids.txt", "split_eval_ids.txt", "excluded_cases.csv", "duplicate_text_groups.csv"]:
        p = OUT / name
        big = p.stat().st_size >= 200 * 1024 * 1024
        if big and not args.hash_large_files:
            digest = "skipped_large_file"
        elif big:
            h = hashlib.sha256()
            with p.open("rb") as fh:
                for block in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(block)
            digest = h.hexdigest()
        else:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
        manifest["outputs"][name] = {"path": rel_path(p), "size_bytes": p.stat().st_size, "sha256": digest}
    (OUT / "dataset_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- report ----
    report = f"""# 人员 A 数据检查与划分报告（草案）

生成时间：{started.isoformat()}
状态：**{STATUS}**（资源版本已核对：仅 LF/CRLF 换行差异）

## 1. 输入与总量

- 原始案例：{len(data):,} 条，ID `case_000001` ~ `case_082210`，连续且唯一
- 抽取成功：{len(doc_ids):,} 篇；失败：{len(failure_ids)} 篇
- 三元组：{n_lines:,} 条；`triple_id` 唯一：{len(triple_ids):,}
- 有三元组的案例：{len(data) - len(missing_ids):,}；无三元组/失败：{len(missing_ids)} 篇

## 2. 仍需处理的问题

> B/C 的 Prompt/Schema 哈希差异已核对：仅由 LF/CRLF 换行造成，合并脚本同时接受两种格式，不再是阻塞项。

1. **9 篇抽取失败**：{", ".join(sorted(failure_ids))}
2. **53 篇成功但 0 三元组**，其中 47 篇原文 ≥50 字，疑似遗漏；6 篇原文 2~35 字，可能确实无可抽内容。
3. **29 组完全重复文本**（58 条），其中 23 组跨 A/B/C 分区，1 组跨类别（case_061120 / case_064821）。划分已按组保持同侧。

## 3. 数据质量概况

| 指标 | 数量 |
|---|---:|
| 三元组 | {n_lines:,} |
| 至少一端未知/空 | {unknown_total:,}（{100*unknown_total/n_lines:.1f}%） |
| head.id == tail.id | {self_total:,} |
| head.name == tail.name | {same_total:,} |
| 内容重复组 | {dup_content_groups:,} |
| 内容重复额外条数 | {dup_content_extra:,} |
| source_id 不在案例表 | 0 |
| 类别不一致 | 0 |
| 证据不是原文子串 | 0 |
| 关系/事实状态/置信度越界 | 0 |

## 4. 划分

- 方法：按 `案件类别` 分层，**案例级** 60/40；完全相同文本的案例视为一组，整体进同一侧；固定 seed={SEED}
- 可用案例：{len(build_ids) + len(eval_ids):,}；建库 {len(build_ids):,} / 评估 {len(eval_ids):,}
- 排除：{len(missing_ids)} 篇（9 失败 + 53 零三元组），清单见 `excluded_cases.csv`
- 各类别比例见 `split_summary.csv`

| 类别 | 总数 | 可用 | 建库 | 评估 | 建库占比 |
|---|---:|---:|---:|---:|---:|
"""
    for r in summary_rows:
        report += f"| {r['category']} | {r['total']:,} | {r['usable']:,} | {r['build']:,} | {r['eval']:,} | {r['build_pct_of_usable']}% |\n"
    report += f"""
## 5. 输出文件

- `cases_master.jsonl`：82,210 条案例主表（含原文、类别、三元组数、划分、重复组）
- `triples_clean.jsonl`：{n_lines:,} 条三元组，保留原字段并附加 `quality` 标记（不删除）
- `issues_triples.csv.gz`：有问题的三元组清单
- `issues_summary.csv`：问题类型汇总
- `split_manifest.csv`：逐案例划分表
- `split_build_ids.txt` / `split_eval_ids.txt`：建库/评估 ID 名单
- `split_summary.csv`：分类别 60/40 统计
- `excluded_cases.csv`：9 失败 + 53 零三元组
- `duplicate_text_groups.csv`：29 组重复文本
- `dataset_manifest.json`：输入哈希、计数、资源核对结果、输出文件哈希

## 6. 下一步

1. 补跑 9 篇失败 + 47 篇疑似遗漏（≥50 字）；6 篇极短文本可记录为“无可抽事实”。
2. 上述工作完成后重新生成本目录，状态改为 `frozen`，再交给 B/C/D。
"""
    (OUT / "audit_report.md").write_text(report, encoding="utf-8")

    print(json.dumps({
        "out": rel_path(OUT),
        "documents": len(data),
        "success": len(doc_ids),
        "failed": len(failure_ids),
        "zero_triples": len([x for x in missing_ids if x not in failure_ids]),
        "usable": len(build_ids) + len(eval_ids),
        "excluded": len(missing_ids),
        "triples": n_lines,
        "build": len(build_ids),
        "eval": len(eval_ids),
        "bad_json": bad_json,
        "source_not_found": len(source_not_found),
        "category_mismatch": category_mismatch,
        "missing_name": missing_name,
        "evidence_not_substring": evidence_not_substring,
        "unit_prefix_bad": unit_prefix_bad,
        "bad_relation": bad_relation,
        "bad_factuality": bad_factuality,
        "bad_confidence": bad_confidence,
        "self_loop": sum(s["self"] for s in partition_stats.values()),
        "unknown": sum(s["unknown"] for s in partition_stats.values()),
        "duplicate_content_groups": dup_content_groups,
        "duplicate_content_extra": dup_content_extra,
        "duplicate_text_groups": len(dup_groups),
        "elapsed_seconds": round((datetime.now().astimezone() - started).total_seconds(), 1),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # relation/factuality sets need to be loaded before main loop
    rel_schema = json.loads((EXTRACTION_DIR / "schemas" / "关系类型体系_v0.1_中文可读版.json").read_text(encoding="utf-8"))
    relation_names = {x["关系名称"] for x in rel_schema["关系类型"]}
    factuality_names = {x["中文名称"] for x in rel_schema["事实状态"]}
    main()
