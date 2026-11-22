import json
import random
from experiments.KEO.configs import config

SEED = 42
DEV_SIZE = 200
TEST_SIZE = 300

def read_jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    demo_meta = read_jsonl(config.QUESTION_METADATA)
    dev_ids = []
    for m in demo_meta:
        dev_ids.extend(m.get("origin_source_ids", []))
    dev_ids = list(dict.fromkeys(dev_ids))   # 去重保序

    splits = config.load_splits()
    eval_ids = splits["eval_ids"]
    dev_set = set(dev_ids)

    rest = [sid for sid in eval_ids if sid not in dev_set]
    rng = random.Random(SEED)
    rng.shuffle(rest)
    test_ids = rest[:TEST_SIZE]

    config.D_SPLITS_DIR.mkdir(parents=True, exist_ok=True)
    config.D_DEV_IDS_FILE.write_text("\n".join(dev_ids), encoding="utf-8")
    config.D_TEST_IDS_FILE.write_text("\n".join(test_ids), encoding="utf-8")
    config.D_SPLITS_MANIFEST.write_text(json.dumps({
        "seed": 42,
        "dev_ids": dev_ids,
        "test_ids": test_ids,
        "note": "dev 从现有 Demo 底牌反查锁定；test 从 eval 排除 dev 后抽样"
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"dev_ids: {len(dev_ids)} 题（来自现有 Demo 底牌）")
    print(f"test_ids: {len(test_ids)} 题（全新，未调参）")

if __name__ == "__main__":
    main()