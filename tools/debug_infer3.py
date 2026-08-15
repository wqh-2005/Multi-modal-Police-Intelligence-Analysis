"""逐步复现 _infer_persons 对 manual_r0 / edge_multiperson 的执行路径。"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.knowledge_schema import Triplet
from app.core.knowledge.storage_service import (
    _is_person_candidate, _is_likely_person, _VICTIM_NAME_HINT, _SUSPECT_NAME_HINT,
    _infer_persons,
)

CASES = {
    "manual_r0": [
        Triplet(subject="李先生", relation="扫码", object="二维码", subject_type="PERSON", object_type="OTHER"),
        Triplet(subject="李先生", relation="加入", object="投资-特训营", subject_type="PERSON", object_type="PLATFORM"),
        Triplet(subject="李先生", relation="添加", object="客服人员", subject_type="PERSON", object_type="PERSON"),
        Triplet(subject="客服人员", relation="冒充", object="基金经理", subject_type="PERSON", object_type="PERSON"),
        Triplet(subject="客服人员", relation="发送", object="安装包", subject_type="PERSON", object_type="OTHER"),
        Triplet(subject="李先生", relation="下载", object="投资平台软件", subject_type="PERSON", object_type="PLATFORM"),
        Triplet(subject="客服人员", relation="诱导", object="李先生", subject_type="PERSON", object_type="PERSON"),
        Triplet(subject="李先生", relation="转账", object="平台", subject_type="PERSON", object_type="PLATFORM"),
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
    persons = {n for n in person_set if _is_person_candidate(n)}
    pure_actors = {n for n in persons if subj_count.get(n, 0) > 0 and obj_count.get(n, 0) == 0}
    print("subj_count:", dict(subj_count))
    print("obj_count:", dict(obj_count))
    print("pure_actors:", pure_actors)
    v, s = _infer_persons(triplets)
    print("=> _infer_persons:", v.name, "/", s.name)
