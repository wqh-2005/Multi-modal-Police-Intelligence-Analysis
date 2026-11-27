"""D-08：独立测试集 —— 类别均衡抽样（使用项目真实路径）."""
import json
import random
from collections import defaultdict
from pathlib import Path

from experiments.KEO.configs import config

SEED = 42
TEST_SIZE = 300

SPLITS_FILE = config.SPLITS_PATH                    # output/v1.0/splits/划分名单.json
DEMO_META = config.DATA_DIR / "questions" / "Demo版" / "question_metadata.jsonl"
FULL_META = config.QUESTION_METADATA_FULL           # 全量底牌
DEV_IDS_OUT = config.DATA_DIR / "splits" / "d_dev_ids.txt"
TEST_IDS_OUT = config.DATA_DIR / "splits" / "d_test_ids.txt"
MANIFEST_OUT = config.DATA_DIR / "splits" / "d_splits.json"


def read_jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    # dev_ids 从 Demo 底牌反查
    dev_ids = []
    for m in read_jsonl(DEMO_META):
        dev_ids.extend(m.get("origin_source_ids", []))
    dev_ids = list(dict.fromkeys(dev_ids))
    dev_set = set(dev_ids)

    # eval_ids 从划瓆单
    splits = json.loads(Path(SPLITS_FILE).read_text(encoding="utf-8"))
    eval_ids = set(splits["eval_ids"])
    candidate = eval_ids - dev_set

    # source_id -> 类别（用全量底牌）
    source_to_cat = {}
    for meta in read_jsonl(FULL_META):
        cat = meta.get("gold_category")
        if not cat:
            continue
        for sid in meta.get("origin_source_ids", []):
            source_to_cat[sid] = cat

    # 按类别分组
    by_cat = defaultdict(list)
    for sid in candidate:
        cat = source_to_cat.get(sid)
        if cat:
            by_cat[cat].append(sid)

    # 均衡抽：每类均摊 TEST_SIZE/类别数，小类不足全取
    per_cat_limit = max(TEST_SIZE // len(by_cat), 1)
    rng = random.Random(SEED)
    chosen = set()
    test_ids = []
    per_cat_picked = {}
    for cat, sids in sorted(by_cat.items()):
        shuffled = [s for s in sids if s not in chosen]
        rng.shuffle(shuffled)
        picked = shuffled[:per_cat_limit]
        test_ids.extend(picked)
        chosen.update(picked)
        per_cat_picked[cat] = len(picked)

    # 不足则补
    if len(test_ids) < TEST_SIZE:
        remaining = [s for cat, sids in by_cat.items() for s in sids if s not in chosen]
        rng.shuffle(remaining)
        test_ids.extend(remaining[:TEST_SIZE - len(test_ids)])

    # 写入
    Path(DEV_IDS_OUT).parent.mkdir(parents=True, exist_ok=True)
    Path(DEV_IDS_OUT).write_text("\n".join(dev_ids), encoding="utf-8")
    Path(TEST_IDS_OUT).write_text("\n".join(test_ids), encoding="utf-8")
    Path(MANIFEST_OUT).write_text(json.dumps({
        "seed": SEED, "test_ids": test_ids, "dev_ids": dev_ids,
        "sampling": "category-balanced", "per_cat_limit": per_cat_limit,
        "per_cat_picked": per_cat_picked,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"dev_ids: {len(dev_ids)}")
    print(f"test_ids: {len(test_ids)}（均衡抽样）每类上限 {per_cat_limit}")
    print("各类:", dict(sorted(per_cat_picked.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()