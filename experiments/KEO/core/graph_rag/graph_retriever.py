"""从节点向量种子检索Neo4j有限子图，不生成最终答案。"""

import argparse
import asyncio
import json
import logging

from logging import getLogger
from time import perf_counter
from typing import Optional

from experiments.KEO.core.graph_rag.neo4j_store import Neo4jStore
from experiments.KEO.core.graph_rag.node_index import NodeIndex
from experiments.KEO.models.graph_schema import (
    GraphRetrievalResult,
    GraphSubgraph,
)


logger = getLogger(__name__)


class GraphRetriever:
    """组织种子检索及有限邻域扩展。

    NodeIndex负责向量检索，Neo4jStore负责受限邻域查询。
    本类不重复实现Cypher，不读取全图、不写数据库或向量集合。
    传入的store和node_index由调用方管理生命周期。
    """

    def __init__(self, store: Neo4jStore, node_index: NodeIndex):
        if node_index.store is not store:
            raise ValueError("NodeIndex和GraphRetriever必须使用同一个Neo4jStore实例")

        self.store = store
        self.node_index = node_index
        self.settings = store.settings

    @staticmethod
    def _check_limit(value: int, maximum: int, name: str) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise ValueError(f"{name}必须为1到{maximum}之间的整数")

    def _check_subgraph(self, subgraph: GraphSubgraph, seed_ids: list[str]) -> None:
        """防御性核验返回结构，不通过静默裁剪掩盖上游错误。"""

        if len(subgraph.nodes) > self.settings.MAX_NODES:
            raise RuntimeError("Neo4j返回的子图节点数量超过MAX_NODES")
        if len(subgraph.edges) > self.settings.MAX_EDGES:
            raise RuntimeError("Neo4j返回的子图事实数量超过MAX_EDGES")

        node_ids = {node.node_id for node in subgraph.nodes}
        if len(node_ids) != len(subgraph.nodes):
            raise RuntimeError("查询子图包含重复node_id")
        if not set(seed_ids).issubset(node_ids):
            raise RuntimeError("部分向量种子在Neo4j子图中不存在，请检查索引一致性")

        triple_ids = set()
        for edge in subgraph.edges:
            if edge.triple_id in triple_ids:
                raise RuntimeError("查询子图包含重复triple_id")
            triple_ids.add(edge.triple_id)
            if edge.head_node_id not in node_ids or edge.tail_node_id not in node_ids:
                raise RuntimeError("查询子图的事实端点不存在")

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        hops: Optional[int] = None,
    ) -> GraphRetrievalResult:
        """检索一个问题对应的有限逻辑子图。

        正常无种子时返回空子图；有种子但无关系时保留孤立种子。
        低相似度不等于严格无匹配，本层不增加未经配置的分数阈值。
        未就绪、配置冲突、版本变化及服务异常均向调用方抛出，
        由最终api.py转换为格式1.3的error，不伪装成空结果。
        """

        if not isinstance(query, str) or not query.strip():
            raise ValueError("query必须是非空字符串")

        seed_limit = min(self.settings.SEED_TOP_K, self.settings.MAX_NODES)
        top_k = seed_limit if top_k is None else top_k
        hops = self.settings.MAX_HOPS if hops is None else hops
        self._check_limit(top_k, seed_limit, "top_k")
        self._check_limit(hops, self.settings.MAX_HOPS, "hops")

        started = perf_counter()

        # NodeIndex.index_key在其首次初始化后才能读取，因此先记录
        # Neo4j版本，再调用search，随后用完整索引身份校验同一版本。
        state = await self.store.get_state()
        if state.status != "ready":
            raise RuntimeError("知识库尚未ready，请先完成导入与节点向量同步")
        if state.embedding_fingerprint != self.node_index.fingerprint:
            raise RuntimeError("当前Embedding配置与知识库指纹不一致")
        version = state.version

        seeds = await self.node_index.search(query.strip(), top_k=top_k)
        fingerprint = self.node_index.fingerprint
        index_key = self.node_index.index_key

        await self.store.assert_query_ready(
            fingerprint,
            index_key,
            expected_version=version,
        )

        if len(seeds) > top_k:
            raise RuntimeError("向量检索返回的种子数量超过请求上限")
        seed_ids = [seed.node_id for seed in seeds]
        if len(set(seed_ids)) != len(seed_ids):
            raise RuntimeError("向量检索返回了重复种子节点")

        if seed_ids:
            subgraph = await self.store.get_subgraph(
                seed_ids=seed_ids,
                fingerprint=fingerprint,
                index_key=index_key,
                expected_version=version,
                hops=hops,
            )
            self._check_subgraph(subgraph, seed_ids)
        else:
            subgraph = GraphSubgraph()

        # 校验完整流程；后续读取证据和输出上下文时仍须沿用该版本。
        await self.store.assert_query_ready(
            fingerprint,
            index_key,
            expected_version=version,
        )

        result = GraphRetrievalResult(
            seeds=seeds,
            subgraph=subgraph,
            version=version,
            fingerprint=fingerprint,
            index_key=index_key,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
        )
        logger.info(
            "有限子图检索完成：种子=%d，节点=%d，事实=%d，版本=%d，耗时=%.2fms",
            len(result.seeds), len(subgraph.nodes), len(subgraph.edges),
            version, result.elapsed_ms,
        )
        return result


async def main() -> None:
    parser = argparse.ArgumentParser(description="图谱RAG有限子图检索")
    parser.add_argument("--query", required=True, help="检索问题")
    parser.add_argument("--top-k", type=int, help="种子数，不超过配置上限")
    parser.add_argument("--hops", type=int, help="逻辑跳数，不超过配置上限")
    args = parser.parse_args()

    async with Neo4jStore() as store:
        index = NodeIndex(store)
        try:
            retriever = GraphRetriever(store, index)
            result = await retriever.retrieve(
                query=args.query,
                top_k=args.top_k,
                hops=args.hops,
            )
            print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
        finally:
            await index.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(main())
