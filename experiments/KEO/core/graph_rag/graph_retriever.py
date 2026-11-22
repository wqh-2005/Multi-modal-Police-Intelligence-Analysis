"""节点向量与证据全文两路召回，按案例形成有限子图，不生成最终答案。"""

import argparse
import asyncio
import json
import logging
from collections import defaultdict

from logging import getLogger
from time import perf_counter
from typing import Optional

from experiments.KEO.core.graph_rag.neo4j_store import Neo4jStore
from experiments.KEO.core.graph_rag.node_index import NodeIndex
from experiments.KEO.core.graph_rag.relevance import lexical_scores
from experiments.KEO.models.graph_schema import (
    GraphRetrievalResult,
    GraphSubgraph,
)


logger = getLogger(__name__)


class GraphRetriever:
    """组织种子、证据召回与同案有限关系扩展。

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

    def _rank_cases(self, query, node_graph, evidence_graph, hits):
        """候选内词面重排加两路倒数排名融合，不比较两类原始分数。"""
        nodes = {n.node_id:n for g in (node_graph,evidence_graph) for n in g.nodes}
        facts = {e.triple_id:e for g in (node_graph,evidence_graph) for e in g.edges}
        evidence_texts = defaultdict(list)
        evidence_rank = {}
        for rank, hit in enumerate(hits, 1):
            for tid in hit.triple_ids:
                if tid in facts and facts[tid].source_id != hit.source_id:
                    raise RuntimeError("全文证据与关联事实来源不一致")
                evidence_texts[tid].append(hit.text)
                evidence_rank.setdefault(tid, rank)
        ids = sorted(facts)
        fact_texts = [f"{nodes[facts[t].head_node_id].name} {facts[t].relation} "
                      f"{nodes[facts[t].tail_node_id].name}" for t in ids]
        text_scores = dict(zip(ids, lexical_scores(query, [
            text + " " + " ".join(evidence_texts[tid]) for tid,text in zip(ids,fact_texts)])))
        node_ids = {e.triple_id for e in node_graph.edges}
        node_order = sorted(node_ids,key=lambda t:(-text_scores[t],t))
        node_rank = {tid:rank for rank,tid in enumerate(node_order,1)}
        scores = {}
        grouped = defaultdict(list)
        for tid in ids:
            rrf = sum(61 / (60 + ranks[tid]) for ranks in (node_rank,evidence_rank) if tid in ranks) / 2
            scores[tid] = 0.65 * text_scores[tid] + 0.35 * rrf
            grouped[facts[tid].source_id].append(tid)
        for source in grouped:
            grouped[source].sort(key=lambda tid:(-scores[tid],tid))
        # 同一案例重复命中不无限加分，使用前两条事实的平均得分。
        sources = sorted(grouped,key=lambda source:(
            -sum(scores[t] for t in grouped[source][:2]) / min(2,len(grouped[source])),source))
        sources = sources[:min(self.settings.RETRIEVAL_MAX_CASES,self.settings.CONTEXT_MAX_CASES)]
        cases = {source:grouped[source][:min(self.settings.CASE_ANCHOR_FACTS,
                                           self.settings.CASE_FACT_CANDIDATES)] for source in sources}
        return cases, nodes, facts

    def _merge_final(self, seeds, cases, nodes, facts, expanded):
        """命中事实优先入图，补充事实限于入选案例，保留孤立向量种子。"""
        final_nodes = {sid:nodes[sid] for sid in seeds if sid in nodes}
        final_edges = {}
        all_nodes = {**nodes, **{n.node_id:n for n in expanded.nodes}}
        all_facts = {**facts, **{e.triple_id:e for e in expanded.edges}}
        anchors = [tid for ids in cases.values() for tid in ids]
        ordered = anchors + [e.triple_id for e in expanded.edges if e.triple_id not in anchors]
        for tid in ordered:
            edge = all_facts.get(tid)
            if edge is None or edge.source_id not in cases:
                continue
            if len(final_edges) >= self.settings.MAX_EDGES:
                break
            endpoints = {edge.head_node_id,edge.tail_node_id}
            if len(final_nodes.keys() | endpoints) > self.settings.MAX_NODES:
                continue
            for node_id in endpoints:
                final_nodes[node_id] = all_nodes[node_id]
            final_edges[tid] = edge
        return GraphSubgraph(nodes=list(final_nodes.values()),edges=list(final_edges.values()))

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        hops: Optional[int] = None,
    ) -> GraphRetrievalResult:
        """检索一个问题对应的有限逻辑子图。

        节点、证据均无命中时返回空子图；保留孤立向量种子。
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

        hits, cases = [], {}
        recall_enabled = self.settings.EVIDENCE_RECALL_ENABLED
        if seed_ids:
            subgraph = await self.store.get_subgraph(
                seed_ids=seed_ids,
                fingerprint=fingerprint,
                index_key=index_key,
                expected_version=version,
                hops=1 if recall_enabled else hops,
                query=query.strip(),
            )
            self._check_subgraph(subgraph, seed_ids)
        else:
            subgraph = GraphSubgraph()

        if recall_enabled:
            hits = await self.store.search_evidence(query.strip(), fingerprint, index_key, version)
            hit_cases = {}
            for hit in hits:
                hit_cases.setdefault(hit.source_id, [])
                hit_cases[hit.source_id].extend(hit.triple_ids)
            hit_cases = {source:list(dict.fromkeys(ids))[:self.settings.MAX_EDGES]
                         for source,ids in hit_cases.items() if ids}
            evidence_graph = await self.store.get_case_facts(
                hit_cases, fingerprint, index_key, version, candidates=True)
            cases, nodes, facts = self._rank_cases(query, subgraph, evidence_graph, hits)
            expanded = await self.store.get_case_facts(
                cases, fingerprint, index_key, version,
                expand=self.settings.CASE_EXPANSION_ENABLED and hops > 1)
            subgraph = self._merge_final(seed_ids, cases, nodes, facts, expanded)
            self._check_subgraph(subgraph, seed_ids)
            logger.info("两路召回：证据命中=%d，入选案例=%d，最终事实=%d",
                        len(hits),len(cases),len(subgraph.edges))

        # 校验完整流程；后续读取证据和输出上下文时仍须沿用该版本。
        await self.store.assert_query_ready(
            fingerprint,
            index_key,
            expected_version=version,
        )

        result = GraphRetrievalResult(
            query=query.strip(),
            seeds=seeds,
            evidence_hits=hits,
            selected_source_ids=list(cases),
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
