"""逐步复现 _infer_persons 对 edge_multiperson 的执行路径。"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.knowledge_schema import Triplet
from app.core.knowledge.storage_service import (
    _is_person_candidate, _is_likely_person, _VICTIM_NAME_HINT, _SUSPECT_NAME_HINT,
    _infer_persons,
)

triplets = [
    Triplet(subject="张某", relation="合谋", object="李某", subject_type="PERSON", object_type="PERSON"),
    Triplet(subject="张某", relation="合谋", object="王某", subject_type="PERSON", object_type="PERSON"),
    Triplet(subject="李某", relation="合谋", object="王某", subject_type="PERSON", object_type="PERSON"),
    Triplet(subject="张某、李某、王某三人", relation="骗取", object="赵某", subject_type="PERSON", object_type="PERSON"),
    Triplet(subject="赵某", relation="转账", object="50万元", subject_type="PERSON", object_type="AMOUNT"),
]

person_set = {t.subject for t in triplets if t.subject_type == "PERSON"} | {t.object for t in triplets if t.object_type == "PERSON"}
subj_count = Counter(t.subject for t in triplets if t.subject_type == "PERSON")
obj_count = Counter(t.object for t in triplets if t.object_type == "PERSON")
persons = {n for n in person_set if _is_person_candidate(n)}
print("persons:", persons)
print("obj_count 迭代顺序:", list(obj_count))

# 复现策略1 victims 选择
def _victim_rank(n):
    return (obj_count.get(n, 0), 1 if _VICTIM_NAME_HINT.search(n) else 0, 1 if _is_likely_person(n) else 0)

ranked = sorted(obj_count, key=_victim_rank, reverse=True)
print("ranked:", [(n, _victim_rank(n)) for n in ranked])
top = next((n for n in ranked if n not in {"张某"} and (_is_likely_person(n) or _VICTIM_NAME_HINT.search(n))), None)
print("top(likely/hint):", top)
fallback = next((n for n in ranked if n not in {"张某"}), None)
print("fallback:", fallback)

# 复现策略1.5
aggressive = {"威胁", "要求", "要求下载", "要求转账", "要求填写", "诱导", "拨打电话", "骗取", "诈骗", "冒充", "自称", "设局"}
attacked = {t.object for t in triplets if t.relation in aggressive and t.object in persons}
print("attacked:", attacked)
attacked_new = attacked - {"张某"}
print("attacked_new:", attacked_new)

v, s = _infer_persons(triplets)
print("最终:", v.name, "/", s.name)
