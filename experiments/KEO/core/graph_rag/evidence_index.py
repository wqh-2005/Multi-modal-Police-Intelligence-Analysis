"""显式初始化现有 KEOEvidence.text 的全文索引，不导入数据或计算向量。"""

import asyncio
import json

from experiments.KEO.core.graph_rag.neo4j_store import Neo4jStore


async def main():
    async with Neo4jStore() as store:
        info = await store.initialize_evidence_index()
        print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
