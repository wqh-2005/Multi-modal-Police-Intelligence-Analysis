"""Prepare review candidates from completed extraction caches; never call an API."""
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
SEED = 20261012


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def occurrences(text, name):
    if not name or name in {"未知", "不详", "未知人员"}:
        return []
    return [(m.start(), m.end()) for m in re.finditer(re.escape(name), text)]


def convert(row, cached):
    text = row["案情描述"]
    entities, entity_index, relations = [], {}, []
    mention_no = 0
    for triple in cached["triples"]:
        for side in ("head", "tail"):
            item = triple[side]
            key = (item["name"], item["type"], item.get("role"))
            if key in entity_index:
                continue
            eid = f"E{len(entities) + 1}"
            mentions = []
            for start, end in occurrences(text, item["name"]):
                mention_no += 1
                mentions.append({"mention_id": f"M{mention_no}", "start": start,
                                 "end": end, "text": text[start:end]})
            entity = {"entity_id": eid, "canonical_name": item["name"],
                      "type": item["type"], "role": item.get("role"),
                      "mentions": mentions,
                      "review_status": "待人工审核",
                      "alignment_status": "字面匹配待确认" if mentions else "需人工补充提及或确认隐含实体"}
            entity_index[key] = entity
            entities.append(entity)

    # Match source units using the extraction pipeline's exact splitting rules.
    units, cursor = {}, 0
    for part in re.split(r"(?<=[。！？!?；;])", text.strip()):
        part = part.strip()
        if not part:
            continue
        start = text.find(part, cursor)
        if start < 0:
            raise ValueError("Cannot reconstruct source unit")
        cursor = start + len(part)
        uid = f"{row['id']}_clause_{len(units) + 1:03d}"
        units[uid] = {"unit_id": uid, "start": start, "end": cursor, "text": part}

    for triple in cached["triples"]:
        def entity_for(side):
            item = triple[side]
            return entity_index[(item["name"], item["type"], item.get("role"))]

        head, tail = entity_for("head"), entity_for("tail")
        evidence, flags = [], []
        for source in triple.get("evidence", []):
            unit = units.get(source.get("unit_id"))
            if unit and unit["text"] == source.get("text"):
                evidence.append(dict(unit))
            else:
                evidence.append({"unit_id": source.get("unit_id"), "start": None,
                                 "end": None, "text": source.get("text", "")})
                flags.append("证据无法按原文单元对齐")

        def candidates(entity):
            aligned = [m for m in entity["mentions"] if any(
                ev["start"] is not None and ev["start"] <= m["start"] and m["end"] <= ev["end"]
                for ev in evidence)]
            return aligned or entity["mentions"]

        hs, ts = candidates(head), candidates(tail)
        pairs = []
        # Never assign a first occurrence when several mentions are possible.
        if len(hs) == len(ts) == 1:
            pairs = [{"head_mention_id": hs[0]["mention_id"],
                      "tail_mention_id": ts[0]["mention_id"]}]
            flags.append("唯一字面候选对，仍需确认共指和关系证据")
        else:
            flags.append("实体提及缺失或多次出现，需人工填写mention_pairs")
        relations.append({"relation_id": f"R{len(relations) + 1}",
                          "head_entity_id": head["entity_id"], "relation": triple["relation"],
                          "tail_entity_id": tail["entity_id"], "mention_pairs": pairs,
                          "candidate_head_mention_ids": [m["mention_id"] for m in hs],
                          "candidate_tail_mention_ids": [m["mention_id"] for m in ts],
                          "factuality": triple.get("factuality", "不确定"), "evidence": evidence,
                          "review_status": "待人工审核", "review_notes": list(dict.fromkeys(flags)),
                          "llm_provenance": {"triple_id": triple.get("triple_id"),
                                             "confidence": triple.get("confidence")}})
    return {"source_id": row["id"], "source_type": "case",
            "fraud_category": row.get("案件类别", ""), "text": text,
            "schema_version": "1.0", "annotation_status": "待人工审核",
            "label_source": "已有大模型抽取结果转换，非人工金标",
            "entities": entities, "relations": relations,
            "review": {"reviewer": "", "reviewed_at": "", "notes": "", "checked_for_missing_relations": False},
            "provenance": {"model": cached.get("model"), "completed_at": cached.get("completed_at"),
                           "original_text_sha256": hashlib.sha256(text.encode()).hexdigest()}}


def validate(doc):
    text = doc["text"]
    es = {e["entity_id"]: e for e in doc["entities"]}
    ms = {}
    for e in es.values():
        for m in e["mentions"]:
            assert 0 <= m["start"] < m["end"] <= len(text)
            assert text[m["start"]:m["end"]] == m["text"]
            assert m["mention_id"] not in ms
            ms[m["mention_id"]] = e["entity_id"]
    for rel in doc["relations"]:
        assert rel["head_entity_id"] in es and rel["tail_entity_id"] in es
        for pair in rel["mention_pairs"]:
            assert ms[pair["head_mention_id"]] == rel["head_entity_id"]
            assert ms[pair["tail_mention_id"]] == rel["tail_entity_id"]
        for ev in rel["evidence"]:
            if ev["start"] is not None:
                assert text[ev["start"]:ev["end"]] == ev["text"]
    assert doc["annotation_status"] == "待人工审核"


def main():
    raw_path = PROJECT / "data/raw/诈骗案例数据集_带编号.json"
    cache_root = PROJECT / "app/relationextraction/output/case/by_document"
    splits = read(ROOT.parent / "datasets/case_weak_v1/split_manifest.json")["splits"]
    train_ids = set(splits["train"])
    rows = read(raw_path)
    pool = defaultdict(list)
    for row in rows:
        sid = row["id"]
        if sid not in train_ids or not row.get("案情描述", "").strip():
            continue
        path = cache_root / hashlib.sha1(sid.encode()).hexdigest()[:2] / f"{sid}.json"
        if path.is_file():
            pool[row.get("案件类别", "其它类")].append((row, path))
    categories = sorted(pool, key=lambda c: (-len(pool[c]), c))
    rng, selected, seen_text = random.Random(SEED), [], set()
    for i, category in enumerate(categories):
        quota = 100 // len(categories) + (i < 100 % len(categories))
        ordered = sorted(pool[category], key=lambda x: len(x[0]["案情描述"]))
        buckets = [ordered[j * len(ordered) // 3:(j + 1) * len(ordered) // 3] for j in range(3)]
        for bucket in buckets:
            rng.shuffle(bucket)
        count, turn = 0, 0
        while count < quota and any(buckets):
            bucket = buckets[turn % 3]
            turn += 1
            if not bucket:
                continue
            row, path = bucket.pop()
            key = re.sub(r"\s+", "", row["案情描述"])
            if key in seen_text:
                continue
            cached = read(path)
            if cached.get("status") != "success" or not cached.get("triples"):
                continue
            doc = convert(row, cached)
            validate(doc)
            selected.append((row, path, doc))
            seen_text.add(key)
            count += 1
    assert len(selected) == 100, f"Only {len(selected)} documents selected"
    targets = [ROOT / "raw/selected_cases_100.json", ROOT / "selection_manifest.json"]
    targets += [ROOT / "llmdata/case" / f"{r['id']}.json" for r, _, _ in selected]
    if any(path.exists() for path in targets):
        raise FileExistsError("Review batch already exists; will not overwrite human edits")
    write_new(targets[0], [row for row, _, _ in selected])
    stats = Counter()
    for row, path, doc in selected:
        write_new(ROOT / "llmdata/case" / f"{row['id']}.json", doc)
        stats["relations"] += len(doc["relations"])
        stats["entities"] += len(doc["entities"])
        stats["entities_without_literal_mentions"] += sum(not e["mentions"] for e in doc["entities"])
        stats["relations_needing_mention_pairs"] += sum(not r["mention_pairs"] for r in doc["relations"])
        stats["unmatched_evidence"] += sum(ev["start"] is None for r in doc["relations"] for ev in r["evidence"])
    manifest = {"seed": SEED, "documents": 100, "original_dataset_documents": len(rows),
                "selection": "已有训练划分内、成功抽取且非空的案例；类别均衡，按长度三档抽样，去除完全重复文本",
                "purpose": "人工训练标注试点，不用作独立测试集；存在已抽取及非空筛选偏差",
                "category_counts": dict(Counter(r.get("案件类别", "") for r, _, _ in selected)),
                "statistics": dict(stats), "source_ids": [r["id"] for r, _, _ in selected],
                "cache_files": [str(path) for _, path, _ in selected],
                "source_file": str(raw_path), "annotation_status": "全部待人工审核"}
    write_new(ROOT / "selection_manifest.json", manifest)
    # Freeze the schema definitions used during this review batch.
    for filename in ["实体类型体系_v0.1_中文可读版.json", "关系类型体系_v0.1_中文可读版.json"]:
        target = ROOT / "schemas" / filename
        if not target.exists():
            write_new(target, read(PROJECT / "app/relationextraction/schemas" / filename))
    print(json.dumps({k: manifest[k] for k in ["documents", "category_counts", "statistics"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
