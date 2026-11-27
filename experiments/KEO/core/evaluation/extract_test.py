"""按 d_test_ids.txt 从全量（底牌中转）抽取 test 题库 + 底牌。"""
import json
from pathlib import Path
from experiments.KEO.configs import config

DATA = config.DATA_DIR
QUESTIONS_FULL = config.D_QUESTION_FULL
METADATA_FULL = config.QUESTION_METADATA_FULL
TEST_IDS_FILE = DATA / "splits" / "d_test_ids.txt"
OUT_QUESTIONS = DATA / "questions" / "questions_test.jsonl"
OUT_METADATA = DATA / "questions" / "question_metadata_test.jsonl"


def read_jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    test_ids = [l.strip() for l in Path(TEST_IDS_FILE).read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"test_ids: {len(test_ids)} 个")

    sid_to_m = {}
    for m in read_jsonl(METADATA_FULL):
        for sid in m.get("origin_source_ids", []):
            sid_to_m[sid] = m

    qid_to_q = {q["question_id"]: q for q in read_jsonl(QUESTIONS_FULL)}

    out_q, out_m, missing = [], [], []
    for i, sid in enumerate(test_ids, start=1):
        qid_new = f"q{i:05d}"
        m = sid_to_m.get(sid)
        if m is None:
            missing.append(sid)
            continue
        src_qid = m["question_id"]
        q = qid_to_q.get(src_qid)
        if q is None:
            missing.append(sid)
            continue
        q2 = dict(q); q2["question_id"] = qid_new
        m2 = dict(m); m2["question_id"] = qid_new
        out_q.append(q2)
        out_m.append(m2)

    Path(OUT_QUESTIONS).parent.mkdir(parents=True, exist_ok=True)
    with Path(OUT_QUESTIONS).open("w", encoding="utf-8") as f:
        for r in out_q:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with Path(OUT_METADATA).open("w", encoding="utf-8") as f:
        for r in out_m:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"写入题库 {len(out_q)} 题 -> {OUT_QUESTIONS}")
    print(f"写入底牌 {len(out_m)} 条 -> {OUT_METADATA}")
    if missing:
        print(f"⚠️ {len(missing)} 个 source_id 在全量里找不到：{missing[:5]}")


if __name__ == "__main__":
    main()