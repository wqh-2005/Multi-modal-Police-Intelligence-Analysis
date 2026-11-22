import json
from experiments.KEO.configs import config
from pathlib import Path

def read_jsonl(p):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

def sids_from_ids(path):
    return set(p.strip() for p in path.read_text(encoding="utf-8").splitlines() if p.strip())

def sids_from_meta(rows):
    s = set()
    for r in rows:
        s.update(r.get("origin_source_ids", []))
    return s

dev_ids   = sids_from_ids(config.D_DEV_IDS_FILE)        # make_splits 生成
demo_meta = sids_from_meta(read_jsonl(config.QUESTION_METADATA))   # Demo 底牌
# 如果你有 d_dev_ids 对应的题库，也可以对比它的 source_id

print(f"d_dev_ids.txt 的 source_id 数: {len(dev_ids)}")
print(f"Demo底牌 origin_source_ids 数: {len(demo_meta)}")
print(f"两者是否一致: {dev_ids == demo_meta}")
if dev_ids != demo_meta:
    print(f"  仅出现在 dev_ids 的: {sorted(dev_ids - demo_meta)[:5]}...")
    print(f"  仅出现在 Demo底牌 的: {sorted(demo_meta - dev_ids)[:5]}...")