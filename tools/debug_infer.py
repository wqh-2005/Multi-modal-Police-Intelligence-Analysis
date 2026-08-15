"""调试 _infer_persons 中间状态：重现 edge_deepfake / edge_multiperson。"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.knowledge_schema import Triplet
from app.core.knowledge.storage_service import (
    _infer_persons, _is_person_candidate, _is_likely_person, _VICTIM_NAME_HINT,
    _SUSPECT_NAME_HINT,
)

CASES = {
    "edge_deepfake": [
        Triplet(subject="对方", relation="冒充", object="民警", subject_type="PERSON", object_type="PERSON"),
        Triplet(subject="对方", relation="要求转账", object="报警人", subject_type="PERSON", object_type="PERSON"),
        Triplet(subject="报警人", relation="转账", object="5万元", subject_type="PERSON", object_type="AMOUNT"),
    ],
    "edge_multiperson": [
        Triplet(subject="张某", relation="合谋", object="李某", subject_type="PERSON", object_type="PERSON"),
        Triplet(subject="张某", relation="合谋", object="王某", subject_type="PERSON", object_type="PERSON"),
        Triplet(subject="李某", relation="合谋", object="王某", subject_type="PERSON", object_type="PERSON"),
        Triplet(subject="张某、李某、王某三人", relation="骗取", object="赵某", subject_type="PERSON", object_type="PERSON"),
        Triplet(subject="赵某", relation="转账", object="50万元", subject_type="PERSON", object_type="AMOUNT"),
    ],
}

for name, triplets in CASES.items():
    print(f"\n===== {name} =====")
    person_set = {t.subject for t in triplets if t.subject_type == "PERSON"} | {t.object for t in triplets if t.object_type == "PERSON"}
    subj_count = Counter(t.subject for t in triplets if t.subject_type == "PERSON")
    obj_count = Counter(t.object for t in triplets if t.object_type == "PERSON")
    print("person_set:", person_set)
    print("subj_count:", dict(subj_count))
    print("obj_count:", dict(obj_count))
    pure_actors = {n for n in person_set if subj_count.get(n, 0) > 0 and obj_count.get(n, 0) == 0}
    print("pure_actors:", pure_actors)
    print("obj_count.most_common(3):", obj_count.most_common(3))
    for n in person_set:
        print(f"  {n!r}: likely_person={_is_likely_person(n)} victim_hint={bool(_VICTIM_NAME_HINT.search(n))} suspect_hint={bool(_SUSPECT_NAME_HINT.search(n))} candidate={_is_person_candidate(n)}")
    v, s = _infer_persons(triplets)
    print("=> _infer_persons:", v.name, "/", s.name)
