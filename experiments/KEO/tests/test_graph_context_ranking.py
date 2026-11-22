"""Offline regression checks: no Neo4j, Chroma or embedding API calls."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from experiments.KEO.core.graph_rag.context_builder import ContextBuilder
from experiments.KEO.core.graph_rag.neo4j_store import Neo4jStore
from experiments.KEO.core.graph_rag.relevance import lexical_scores
from experiments.KEO.models.graph_schema import (
    EvidenceRecord, GraphEdge, GraphRetrievalResult, GraphSubgraph, NodeMapping,
)


def settings(**changes):
    values = dict(MAX_NODES=100, MAX_EDGES=150, FOREST_EXTRA_FACTS_PER_CASE=2,
                  CONTEXT_FACTS_PER_CASE=6, CONTEXT_MAX_CASES=2,
                  CONTEXT_UNITS_PER_CASE=2, MAX_EVIDENCE_UNITS=20,
                  MAX_CONTEXT_CHARS=1000, CONTEXT_TARGET_CHARS=800)
    values.update(changes)
    return SimpleNamespace(**values)


def node(id, name):
    return NodeMapping(node_id=id, name=name, type="人员", role=None,
                       graph_category="人员", display_name=name, scope="case")


def edge(id, source, head, tail, relation):
    return GraphEdge(triple_id=id, source_id=source, head_node_id=head,
                     tail_node_id=tail, relation=relation, factuality="被要求执行")


def fixture():
    nodes = [node("a", "客服"), node("b", "学生账户"),
             node("c", "投资平台"), node("d", "受害人")]
    facts = [edge("t1", "case1", "a", "b", "注销学生账户"),
             edge("t2", "case1", "a", "b", "清空贷款额度"),
             edge("t3", "case2", "c", "d", "投资盈利")]
    text = "对方以注销学生账户、清空贷款额度为由要求转账。"
    evidence = {"t1": [EvidenceRecord(source_id="case1", unit_id="u1", text=text)],
                "t2": [EvidenceRecord(source_id="case1", unit_id="u2", text=text)],
                "t3": [EvidenceRecord(source_id="case2", unit_id="u3", text="投资平台承诺盈利。")]}
    result = GraphRetrievalResult(query="注销学生账户清空贷款额度。请判断诈骗类型。",
        subgraph=GraphSubgraph(nodes=nodes, edges=facts), version=1,
        fingerprint="fp", index_key="idx", elapsed_ms=0)
    return result, evidence


class RankingTests(unittest.IsolatedAsyncioTestCase):
    def builder(self, evidence, **changes):
        store = SimpleNamespace(settings=settings(**changes),
            assert_query_ready=AsyncMock(), get_evidence_for_facts=AsyncMock(return_value=evidence))
        return ContextBuilder(store), store

    def test_behavior_terms_rank_ahead_of_generic_platform(self):
        scores = lexical_scores("注销学生账户清空贷款额度。请判断诈骗类型。",
            ["客服使用微信平台", "注销学生账户清空贷款额度"])
        self.assertGreater(scores[1], scores[0])

    def test_parallel_relations_are_supplemented(self):
        result, evidence = fixture()
        builder, _ = self.builder(evidence)
        ids = {e.triple_id for e in builder._select_edges(result, evidence)}
        self.assertTrue({"t1", "t2"}.issubset(ids))
        builder.settings.FOREST_EXTRA_FACTS_PER_CASE = 0
        ids = {e.triple_id for e in builder._select_edges(result, evidence)}
        self.assertEqual(len(ids & {"t1", "t2"}), 1)

    async def test_compression_preserves_original_and_fact_provenance(self):
        result, evidence = fixture()
        builder, store = self.builder(evidence)
        output = await builder.build(result)
        self.assertEqual(output.evidence[0].source_id, "case1")
        self.assertEqual(output.evidence[0].text, evidence["t1"][0].text)
        self.assertEqual(set(output.evidence[0].triple_ids), {"t1", "t2"})
        self.assertEqual(len(output.evidence), 2)
        self.assertEqual(output.context.count(evidence["t1"][0].text), 1)
        self.assertIn("被要求执行", output.context)
        self.assertNotIn("节点ID", output.context)
        self.assertLessEqual(len(output.context), builder.settings.CONTEXT_TARGET_CHARS)
        self.assertEqual(store.assert_query_ready.await_count, 2)
        self.assertEqual(store.get_evidence_for_facts.call_args.kwargs["query"], result.query)

    async def test_long_units_are_omitted_not_truncated(self):
        result, evidence = fixture()
        evidence["t1"][0].text = "注销学生账户" * 200
        evidence["t2"][0].text = evidence["t1"][0].text
        builder, _ = self.builder(evidence, MAX_CONTEXT_CHARS=150, CONTEXT_TARGET_CHARS=150)
        output = await builder.build(result)
        self.assertLessEqual(len(output.context), 150)
        self.assertTrue(output.evidence)
        self.assertTrue(all(e.source_id == "case2" for e in output.evidence))

    async def test_case_limit_and_determinism(self):
        result, evidence = fixture()
        builder, _ = self.builder(evidence, CONTEXT_MAX_CASES=1)
        first = await builder.build(result)
        result.subgraph.edges.reverse()
        second = await builder.build(result)
        self.assertEqual(first.context, second.context)
        self.assertEqual({e.source_id for e in first.evidence}, {"case1"})

    async def test_wrong_source_rejected(self):
        result, evidence = fixture()
        evidence["t1"][0].source_id = "case2"
        builder, _ = self.builder(evidence)
        with self.assertRaisesRegex(RuntimeError, "source mismatch"):
            await builder.build(result)

    async def test_same_unit_conflicting_text_rejected(self):
        result, evidence = fixture()
        evidence["t2"][0].unit_id = "u1"
        evidence["t2"][0].text = "different"
        builder, _ = self.builder(evidence)
        with self.assertRaisesRegex(RuntimeError, "Conflicting"):
            await builder.build(result)

    async def test_version_error_is_not_swallowed(self):
        result, evidence = fixture()
        builder, store = self.builder(evidence)
        store.assert_query_ready.side_effect = [None, RuntimeError("version changed")]
        with self.assertRaisesRegex(RuntimeError, "version changed"):
            await builder.build(result)

    async def test_store_ranks_bounded_candidates_before_admission(self):
        result, _ = fixture()
        by_id = {n.node_id: n for n in result.subgraph.nodes}
        facts = [result.subgraph.edges[2], result.subgraph.edges[0]]
        calls = []

        class Rows:
            def __init__(self, rows):
                self.rows = rows

            def __aiter__(self):
                async def iterate():
                    for row in self.rows:
                        yield row
                return iterate()

        class Transaction:
            async def run(self, query, **params):
                calls.append((query, params))
                if "UNWIND $ids" in query:
                    return Rows([{"node": by_id["a"].model_dump()}])
                return Rows([{"fact": e.model_dump(),
                              "head": by_id[e.head_node_id].model_dump(),
                              "tail": by_id[e.tail_node_id].model_dump()} for e in facts])

        store = object.__new__(Neo4jStore)
        store.settings = SimpleNamespace(SEED_TOP_K=5, MAX_HOPS=2, MAX_NODES=4,
            MAX_EDGES=1, MAX_NEIGHBORS_PER_NODE=1, NEIGHBOR_CANDIDATE_FACTOR=4)
        store._load_build_ids = lambda: {"case1", "case2"}
        store._query_state = AsyncMock()

        async def read(work):
            return await work(Transaction())
        store._execute_read = read
        graph = await store.get_subgraph(["a"], "fp", "idx", 1, hops=1, query=result.query)
        self.assertEqual([e.triple_id for e in graph.edges], ["t1"])
        self.assertEqual(calls[1][1]["pool"], 4)
        self.assertLessEqual(len(graph.nodes), 4)
        self.assertEqual(store._query_state.await_count, 2)


if __name__ == "__main__":
    unittest.main()
