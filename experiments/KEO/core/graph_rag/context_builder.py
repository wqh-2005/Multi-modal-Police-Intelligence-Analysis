"""有限子图的最大生成森林筛选、证据读取和上下文重建。"""

import argparse
import asyncio
import json
import logging

from collections import defaultdict
from logging import getLogger
from time import perf_counter
from typing import List

import networkx as nx

from experiments.KEO.core.graph_rag.graph_retriever import GraphRetriever
from experiments.KEO.core.graph_rag.neo4j_store import Neo4jStore
from experiments.KEO.core.graph_rag.node_index import NodeIndex
from experiments.KEO.models.graph_schema import (
    ContextBuildResult,
    GraphEdge,
    GraphRetrievalResult,
    RetrievedGraphEvidence,
)


logger = getLogger(__name__)


class ContextBuilder:
    """只处理一次查询的有限子图，不维护全图。

    森林权重固定为候选子图中同一有向三元组模式
    (head_node_id, relation, tail_node_id)的不同来源案例数。
    这是结构筛选权重，不是全库频次、正确率或抽取置信度。
    等权时按原triple_id插入顺序稳定选择；不合并不同人员。

    无向视图仅用于筛选，生成文字时使用原GraphEdge的方向和状态。
    自环不参加生成树，作为有限补充事实处理。
    """

    def __init__(self, store: Neo4jStore):
        self.store = store
        self.settings = store.settings

    def _select_edges(self, result: GraphRetrievalResult) -> List[GraphEdge]:
        subgraph = result.subgraph
        if len(subgraph.nodes) > self.settings.MAX_NODES:
            raise ValueError("子图节点数超过MAX_NODES")
        if len(subgraph.edges) > self.settings.MAX_EDGES:
            raise ValueError("子图事实数超过MAX_EDGES")

        nodes = {node.node_id: node for node in subgraph.nodes}
        if len(nodes) != len(subgraph.nodes):
            raise ValueError("子图包含重复node_id")

        facts = {}
        support = defaultdict(set)
        for edge in subgraph.edges:
            if edge.triple_id in facts:
                raise ValueError("子图包含重复triple_id")
            if edge.head_node_id not in nodes or edge.tail_node_id not in nodes:
                raise ValueError("子图事实缺少端点")
            facts[edge.triple_id] = edge
            support[(edge.head_node_id, edge.relation, edge.tail_node_id)].add(edge.source_id)

        graph = nx.MultiGraph()
        graph.add_nodes_from(sorted(nodes))
        self_loops = []
        for triple_id in sorted(facts):
            edge = facts[triple_id]
            if edge.head_node_id == edge.tail_node_id:
                self_loops.append(edge)
                continue
            graph.add_edge(
                edge.head_node_id,
                edge.tail_node_id,
                key=edge.triple_id,
                weight=len(support[(edge.head_node_id, edge.relation, edge.tail_node_id)]),
            )

        # 对不连通图返回最大生成森林；保留所选原始事实key。
        selected = [
            facts[key]
            for _, _, key, _ in nx.maximum_spanning_edges(
                graph, algorithm="kruskal", weight="weight", keys=True, data=True,
            )
        ]

        scores = {seed.node_id: seed.score for seed in result.seeds}

        def priority(edge):
            relevance = max(
                scores.get(edge.head_node_id, -1.0),
                scores.get(edge.tail_node_id, -1.0),
            )
            return (-relevance, edge.source_id, edge.triple_id)

        # 种子相关性只决定上下文预算中的展示顺序，不修改树权重。
        selected.sort(key=priority)
        self_loops.sort(key=priority)
        return (selected + self_loops)[:self.settings.MAX_EDGES]

    @staticmethod
    def _fact_line(edge: GraphEdge, nodes: dict) -> str:
        head = nodes[edge.head_node_id]
        tail = nodes[edge.tail_node_id]
        return (
            f"- {head.display_name}（节点ID：{head.node_id}）"
            f" ─{edge.relation}→ "
            f"{tail.display_name}（节点ID：{tail.node_id}）；"
            f"事实状态：{edge.factuality}；三元组ID：{edge.triple_id}"
        )

    async def build(self, result: GraphRetrievalResult) -> ContextBuildResult:
        """根据同一版本的检索结果重建带原文证据的上下文。

        预算不足时省略完整单元，不截断原text。
        没有可用证据的事实不写入上下文。
        未就绪、版本变化或存储异常直接抛出，由api.py统一处理。
        """

        if not isinstance(result, GraphRetrievalResult):
            raise TypeError("result必须是GraphRetrievalResult")
        started = perf_counter()

        async def assert_version():
            await self.store.assert_query_ready(
                result.fingerprint,
                result.index_key,
                expected_version=result.version,
            )

        await assert_version()
        selected = self._select_edges(result)
        facts = {edge.triple_id: edge for edge in selected}
        nodes = {node.node_id: node for node in result.subgraph.nodes}

        evidence_map = {}
        if selected:
            evidence_map = await self.store.get_evidence_for_facts(
                triple_ids=list(facts),
                fingerprint=result.fingerprint,
                index_key=result.index_key,
                expected_version=result.version,
            )
        if not set(evidence_map).issubset(facts):
            raise RuntimeError("证据结果包含未选中的事实")

        # 按事实优先级形成稳定的证据单元顺序，原文每单元只出现一次。
        units = {}
        for edge in selected:
            for evidence in evidence_map.get(edge.triple_id, []):
                if evidence.source_id != edge.source_id:
                    raise RuntimeError("事实与证据来源不一致")
                key = (evidence.source_id, evidence.unit_id)
                if key not in units:
                    units[key] = {"evidence": evidence, "facts": {}}
                elif units[key]["evidence"].text != evidence.text:
                    raise RuntimeError("同一证据单元出现冲突文本")
                units[key]["facts"][edge.triple_id] = edge

        blocks = []
        output_evidence = []
        used_triple_ids = []
        used_triple_set = set()
        context_length = 0

        for unit in units.values():
            if len(output_evidence) >= self.settings.MAX_EVIDENCE_UNITS:
                break

            evidence = unit["evidence"]
            evidence_id = f"E{len(output_evidence) + 1:03d}"
            prefix = (
                f"[{evidence_id}] 来源案例：{evidence.source_id}；"
                f"证据单元：{evidence.unit_id}\n"
                f"原文证据：{evidence.text}\n图谱事实：\n"
            )
            separator_length = 2 if blocks else 0
            budget = self.settings.MAX_CONTEXT_CHARS - context_length - separator_length
            lines = []
            triple_ids = []
            block_length = len(prefix)

            for edge in unit["facts"].values():
                line = self._fact_line(edge, nodes)
                extra_length = len(line) + (1 if lines else 0)
                if block_length + extra_length > budget:
                    continue
                lines.append(line)
                triple_ids.append(edge.triple_id)
                block_length += extra_length

            if not lines:
                continue

            blocks.append(prefix + "\n".join(lines))
            context_length += separator_length + block_length
            output_evidence.append(RetrievedGraphEvidence(
                evidence_id=evidence_id,
                source_id=evidence.source_id,
                text=evidence.text,
                triple_ids=triple_ids,
            ))
            for triple_id in triple_ids:
                if triple_id not in used_triple_set:
                    used_triple_set.add(triple_id)
                    used_triple_ids.append(triple_id)

        # 证据读取之后、返回文字之前再次检查同一知识库版本。
        await assert_version()
        output = ContextBuildResult(
            context="\n\n".join(blocks),
            evidence=output_evidence,
            selected_triple_ids=used_triple_ids,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
        )
        logger.info(
            "上下文重建完成：候选事实=%d，筛选事实=%d，实际采用=%d，证据=%d，字符=%d",
            len(result.subgraph.edges), len(selected), len(used_triple_ids),
            len(output_evidence), len(output.context),
        )
        return output


async def main() -> None:
    parser = argparse.ArgumentParser(description="有限子图筛选及上下文重建")
    parser.add_argument("--query", required=True, help="检索问题")
    parser.add_argument("--top-k", type=int, help="种子数量，不超过配置")
    parser.add_argument("--hops", type=int, help="逻辑跳数，不超过配置")
    args = parser.parse_args()

    async with Neo4jStore() as store:
        index = NodeIndex(store)
        try:
            retriever = GraphRetriever(store, index)
            retrieved = await retriever.retrieve(args.query, top_k=args.top_k, hops=args.hops)
            output = await ContextBuilder(store).build(retrieved)
            print(json.dumps(output.model_dump(), ensure_ascii=False, indent=2))
        finally:
            await index.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
