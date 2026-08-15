"""单用例诊断：edge_deepfake 多次运行观察 LLM 抽取与推断结果。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.knowledge.extraction_service import run_extraction
from app.core.knowledge.storage_service import _infer_persons

TEXT = "对方通过视频通话冒充民警，要求转账到安全账户5万元。"


async def once(i):
    extraction = await run_extraction(TEXT, case_id=f"deepfake_run{i}")
    v, s = _infer_persons(extraction.triplets)
    print(f"\n=== 运行 {i}: victim={v.name!r} suspect={s.name!r} triplets={len(extraction.triplets)}")
    for t in extraction.triplets:
        print(f"  {t.subject}({t.subject_type}) -{t.relation}-> {t.object}({t.object_type})")


async def main():
    for i in range(3):
        await once(i)


if __name__ == "__main__":
    asyncio.run(main())
