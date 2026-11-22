"""有限子图的最大生成森林筛选、证据读取和上下文重建。"""

import argparse
import asyncio
import json
import logging

from collections import defaultdict

from experiments.KEO.core.graph_rag.relevance import lexical_scores
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
    """Query-aware per-case forests with bounded relation supplements.

    Original directed facts and evidence are preserved. Undirected forests are
    only selection views, never inferred relationships between different cases.
    """

    def __init__(self, store: Neo4jStore):
        self.store = store
        self.settings = store.settings

    def _edge_scores(self, result, evidence_map):
        nodes = {n.node_id:n for n in result.subgraph.nodes}
        edges = result.subgraph.edges
        fact_texts = [f"{nodes[e.head_node_id].name} {e.relation} {nodes[e.tail_node_id].name}"
                      for e in edges]
        fact_scores = lexical_scores(result.query, fact_texts)
        evidence_items = {(ev.source_id,ev.unit_id):ev
                          for values in evidence_map.values() for ev in values}
        keys = sorted(evidence_items)
        evidence_scores = dict(zip(keys, lexical_scores(
            result.query, [evidence_items[k].text for k in keys])))
        seed_scores = {s.node_id:max(0.0,s.score) for s in result.seeds}
        return {e.triple_id: 0.35 * score + 0.55 * max(
                    (evidence_scores[(ev.source_id,ev.unit_id)]
                     for ev in evidence_map.get(e.triple_id,[])), default=0.0)
                + 0.10 * max(seed_scores.get(e.head_node_id,0),seed_scores.get(e.tail_node_id,0))
                for e,score in zip(edges,fact_scores)}

    def _select_edges(self, result, evidence_map=None) -> List[GraphEdge]:
        subgraph = result.subgraph
        if len(subgraph.nodes) > self.settings.MAX_NODES or len(subgraph.edges) > self.settings.MAX_EDGES:
            raise ValueError("Subgraph exceeds configured limits")
        nodes = {n.node_id:n for n in subgraph.nodes}
        if len(nodes) != len(subgraph.nodes):
            raise ValueError("Duplicate node_id")
        facts = {}
        for edge in subgraph.edges:
            if edge.triple_id in facts:
                raise ValueError("Duplicate triple_id")
            if edge.head_node_id not in nodes or edge.tail_node_id not in nodes:
                raise ValueError("Missing fact endpoint")
            facts[edge.triple_id] = edge
        scores = self._edge_scores(result, evidence_map or {})
        grouped = defaultdict(list)
        for edge in facts.values():
            if evidence_map is None or evidence_map.get(edge.triple_id):
                grouped[edge.source_id].append(edge)
        selected = []
        for source_id in sorted(grouped):
            case_edges = sorted(grouped[source_id],key=lambda e:(-scores[e.triple_id],e.triple_id))
            graph = nx.MultiGraph()
            for edge in case_edges:
                if edge.head_node_id != edge.tail_node_id:
                    graph.add_edge(edge.head_node_id,edge.tail_node_id,key=edge.triple_id,
                                   weight=scores[edge.triple_id])
            forest_ids = {key for _,_,key,_ in nx.maximum_spanning_edges(
                graph,algorithm="kruskal",weight="weight",keys=True,data=True)}
            chosen = [e for e in case_edges if e.triple_id in forest_ids]
            # Preserve a small number of relevant parallel/cycle/self-loop facts.
            supplements = [e for e in case_edges if e.triple_id not in forest_ids]
            chosen.extend(supplements[:self.settings.FOREST_EXTRA_FACTS_PER_CASE])
            chosen.sort(key=lambda e:(-scores[e.triple_id],e.triple_id))
            selected.extend(chosen[:self.settings.CONTEXT_FACTS_PER_CASE])
        selected.sort(key=lambda e:(-scores[e.triple_id],e.source_id,e.triple_id))
        return selected[:self.settings.MAX_EDGES]

    @staticmethod
    def _fact_line(edge: GraphEdge, nodes: dict) -> str:
        head,tail = nodes[edge.head_node_id],nodes[edge.tail_node_id]
        return f"- {head.display_name} --{edge.relation}--> {tail.display_name} ({edge.factuality})"

    async def build(self, result: GraphRetrievalResult) -> ContextBuildResult:
        """Rank evidence before spending the final budget; never truncate originals."""
        if not isinstance(result,GraphRetrievalResult):
            raise TypeError("Expected GraphRetrievalResult")
        started = perf_counter()
        async def assert_version():
            await self.store.assert_query_ready(result.fingerprint,result.index_key,
                                                expected_version=result.version)
        await assert_version()
        # Validate before any fetch. The fetch is bounded by MAX_EDGES and evidence limits.
        self._select_edges(result)
        evidence_map = {}
        all_facts = {e.triple_id:e for e in result.subgraph.edges}
        if all_facts:
            evidence_map = await self.store.get_evidence_for_facts(
                triple_ids=list(all_facts), fingerprint=result.fingerprint,
                index_key=result.index_key, expected_version=result.version,query=result.query)
        if not set(evidence_map).issubset(all_facts):
            raise RuntimeError("Evidence contains unknown fact")
        units_by_key = {}
        for tid, evidence_list in evidence_map.items():
            for ev in evidence_list:
                if ev.source_id != all_facts[tid].source_id:
                    raise RuntimeError("Fact/evidence source mismatch")
                key = (ev.source_id,ev.unit_id)
                old = units_by_key.get(key)
                if old is not None and old != ev.text:
                    raise RuntimeError("Conflicting evidence text")
                units_by_key[key] = ev.text
        selected = self._select_edges(result,evidence_map)
        scores = self._edge_scores(result,evidence_map)
        nodes = {n.node_id:n for n in result.subgraph.nodes}
        # Identical source text shares one citation even when unit IDs differ.
        units = {}
        for edge in selected:
            for ev in evidence_map.get(edge.triple_id,[]):
                key = (ev.source_id,ev.text)
                unit = units.setdefault(key,{"evidence":ev,"facts":{}})
                unit["facts"][edge.triple_id] = edge
        items = list(units.values())
        text_scores = lexical_scores(result.query,[u["evidence"].text for u in items])
        for unit,score in zip(items,text_scores):
            unit["score"] = 0.6 * score + 0.4 * max(scores[t] for t in unit["facts"])
        items.sort(key=lambda u:(-u["score"],u["evidence"].source_id,u["evidence"].unit_id))
        blocks, output_evidence, used_ids, used = [],[],[],set()
        case_counts = defaultdict(int)
        char_limit = min(self.settings.MAX_CONTEXT_CHARS,self.settings.CONTEXT_TARGET_CHARS)
        context_length = 0
        while items and len(output_evidence) < self.settings.MAX_EVIDENCE_UNITS:
            # Prefer the first useful unit of each case, then additional units.
            idx = min(range(len(items)),key=lambda i:(
                case_counts.get(items[i]["evidence"].source_id, 0),-items[i]["score"],
                items[i]["evidence"].source_id,items[i]["evidence"].unit_id))
            unit = items.pop(idx)
            ev = unit["evidence"]
            if case_counts.get(ev.source_id, 0) >= self.settings.CONTEXT_UNITS_PER_CASE:
                continue
            if ev.source_id not in case_counts and len(case_counts) >= self.settings.CONTEXT_MAX_CASES:
                continue
            remaining = [e for e in unit["facts"].values() if e.triple_id not in used]
            if not remaining:
                continue
            remaining.sort(key=lambda e:(-scores[e.triple_id],e.triple_id))
            evidence_id = f"E{len(output_evidence)+1:03d}"
            prefix = f"[{evidence_id}] \u5386\u53f2\u6848\u4f8b {ev.source_id}\n\u539f\u6587\u8bc1\u636e\uff1a{ev.text}\n\u5173\u952e\u5173\u7cfb\uff1a\n"
            budget = char_limit-context_length-(2 if blocks else 0)
            lines, triple_ids = [],[]
            length = len(prefix)
            for edge in remaining:
                line = self._fact_line(edge,nodes)
                extra = len(line)+(1 if lines else 0)
                if length+extra <= budget:
                    lines.append(line); triple_ids.append(edge.triple_id); length += extra
            if not lines:
                continue
            block = prefix+"\n".join(lines)
            context_length += len(block)+(2 if blocks else 0)
            blocks.append(block)
            case_counts[ev.source_id] += 1
            used.update(triple_ids); used_ids.extend(triple_ids)
            output_evidence.append(RetrievedGraphEvidence(
                evidence_id=evidence_id,source_id=ev.source_id,text=ev.text,triple_ids=triple_ids))
        await assert_version()
        output = ContextBuildResult(context="\n\n".join(blocks),evidence=output_evidence,
            selected_triple_ids=used_ids,elapsed_ms=round((perf_counter()-started)*1000,2))
        logger.info("Context: candidates=%d selected=%d used=%d cases=%d evidence=%d chars=%d",
            len(all_facts),len(selected),len(used_ids),len(case_counts),len(output_evidence),len(output.context))
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
