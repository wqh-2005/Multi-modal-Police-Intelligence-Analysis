from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED_DOCUMENTS = 1197
EXPECTED_TRIPLES = 10497
EXPECTED_SPLITS = {"train": 927, "validation": 180, "test": 90}
EXPECTED_ZERO_DOCUMENTS = {"case_000001", "case_000076", "case_000129"}
EXCLUDED_DOCUMENTS = {"case_v3_000168", "case_v3_000289"}
FINAL_STATUS = "人工确认金标"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    gold = root / "datasets" / "gold_v3"
    quality = root / "datasets" / "quality_v3"
    relation = root / "datasets" / "relation_v3"

    documents = read_jsonl(gold / "all.jsonl")
    triples = read_jsonl(gold / "triples.jsonl")
    manifest = read_json(quality / "merge_manifest.json")
    split_manifest = read_json(quality / "split_manifest.json")

    document_ids = [row["source_id"] for row in documents]
    triple_ids = [row["triple_id"] for row in triples]
    require(len(documents) == EXPECTED_DOCUMENTS, "文档数量不是1197")
    require(len(set(document_ids)) == EXPECTED_DOCUMENTS, "文档ID存在重复")
    require(len(triples) == EXPECTED_TRIPLES, "三元组数量不是10497")
    require(len(set(triple_ids)) == EXPECTED_TRIPLES, "三元组ID存在重复")
    require(not (set(document_ids) & EXCLUDED_DOCUMENTS), "失败文档仍在V3中")

    zero_documents = {row["source_id"] for row in documents if not row["triples"]}
    require(zero_documents == EXPECTED_ZERO_DOCUMENTS, "合法零三元组文档不匹配")
    require(all(row["status"] == FINAL_STATUS for row in triples), "存在非金标状态")
    require(all(row.get("gold_version") == "3.0" for row in documents), "文档版本不是3.0")

    split_sets: dict[str, set[str]] = {}
    for split_name, expected_size in EXPECTED_SPLITS.items():
        rows = read_jsonl(gold / f"{split_name}.jsonl")
        ids = {row["source_id"] for row in rows}
        require(len(rows) == expected_size and len(ids) == expected_size, f"{split_name}规模或ID异常")
        require(ids == set(split_manifest["splits"][split_name]), f"{split_name}与划分清单不一致")
        split_sets[split_name] = ids
    require(not (split_sets["train"] & split_sets["validation"]), "训练集与验证集交叉")
    require(not (split_sets["train"] & split_sets["test"]), "训练集与测试集交叉")
    require(not (split_sets["validation"] & split_sets["test"]), "验证集与测试集交叉")
    require(set().union(*split_sets.values()) == set(document_ids), "划分没有完整覆盖文档")

    label_map = read_json(relation / "label_map.json")
    expected_labels = set(label_map["labels"] if isinstance(label_map, dict) else label_map)
    relation_counts: dict[str, int] = {}
    train_labels: set[str] = set()
    for split_name in EXPECTED_SPLITS:
        rows = read_jsonl(relation / f"{split_name}.jsonl")
        relation_counts[split_name] = len(rows)
        if split_name == "train":
            train_labels = {label for row in rows for label in row["labels"]}
    require(expected_labels <= train_labels, "训练集中缺少关系标签")

    frozen_hash = sha256(relation / "test.jsonl")
    require(frozen_hash == manifest["frozen_relation_test_sha256"], "冻结测试集哈希不匹配")
    for relative_path, expected_hash in manifest["output_files_sha256"].items():
        path = root / Path(relative_path)
        require(path.is_file(), f"缺少输出文件：{relative_path}")
        require(sha256(path) == expected_hash, f"输出文件哈希变化：{relative_path}")

    source_types = Counter(row["source_type"] for row in documents)
    result = {
        "passed": True,
        "documents": len(documents),
        "triples": len(triples),
        "source_types": dict(source_types),
        "zero_triple_documents": sorted(zero_documents),
        "document_splits": EXPECTED_SPLITS,
        "relation_samples": relation_counts,
        "train_relation_labels": len(train_labels),
        "frozen_test_sha256": frozen_hash,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
