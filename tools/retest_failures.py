"""重跑基线失败用例，验证新推断逻辑。

覆盖：冒充领导熟人类 ×2、网络婚恋交友类 ×1、edge_deepfake。
（edge_empty 为设计上的空文本边界用例，不重跑。）
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.knowledge.extraction_service import run_extraction
from app.core.knowledge.storage_service import _infer_persons, _build_transactions
from app.models.knowledge_schema import ExtractionOutput

PROJECT_DIR = Path(__file__).resolve().parent.parent

_SUSPECT_NAME = __import__("re").compile(r"案犯|嫌疑|对方|陌生|客服|组织|群主|骗子|导师|经理|专家")
_VICTIM_NAME = __import__("re").compile(r"被害|受害|事主|报警|报案|被骗")


def evaluate(extraction):
    v, s = _infer_persons(extraction.triplets)
    tx = _build_transactions(extraction.triplets)
    checks = {}
    checks["有结果"] = len(extraction.triplets) > 0
    checks["victim≠未知"] = v.name != "未知"
    checks["suspect≠未知"] = s.name != "未知"
    if checks["victim≠未知"] and checks["suspect≠未知"]:
        checks["victim≠suspect"] = v.name != s.name
    if checks.get("victim≠未知"):
        checks["victim语义正确"] = (not _SUSPECT_NAME.search(v.name)) and (
            _VICTIM_NAME.search(v.name) or len(v.name) <= 5)
    if checks.get("suspect≠未知"):
        checks["suspect语义正确"] = (_SUSPECT_NAME.search(s.name) or s.name in {"A", "B"}
                                     or s.name not in {"报警人", "报案人", "受害人", "被害人",
                                                       "事主", "当事人", "举报人"})
    passed_basic = all(checks.get(k, False) for k in ["有结果", "victim≠未知", "suspect≠未知"])
    return v, s, tx, checks, passed_basic


async def main():
    with open(PROJECT_DIR / "sample" / "诈骗案例数据集_重分类.json", encoding="utf-8") as f:
        data = json.load(f)

    cases = []
    seen = {}
    for item in data:
        cat = item["案件类别"]
        if cat in ("冒充领导熟人类", "网络婚恋、交友类") and seen.get(cat, 0) < 2:
            seen[cat] = seen.get(cat, 0) + 1
            cases.append((f"narr_{cat}_{seen[cat]}", cat, item["案情描述"]))
    cases.append(("edge_deepfake", "边界-换脸场景",
                  "对方通过视频通话冒充民警，要求转账到安全账户5万元。"))

    for case_id, cat, text in cases:
        t0 = time.time()
        try:
            extraction = await run_extraction(text, case_id=case_id)
            v, s, tx, checks, ok = evaluate(extraction)
            print(f"\n[{case_id}] {cat} ({time.time()-t0:.0f}s) triplets={len(extraction.triplets)}")
            print(f"  victim={v.name!r} suspect={s.name!r} tx={len(tx)} 通过基础={ok}")
            print(f"  checks={checks}")
            for t in extraction.triplets:
                print(f"    {t.subject}({t.subject_type}) -{t.relation}-> {t.object}({t.object_type})")
        except Exception as e:
            print(f"[{case_id}] 错误: {e}")


if __name__ == "__main__":
    asyncio.run(main())
