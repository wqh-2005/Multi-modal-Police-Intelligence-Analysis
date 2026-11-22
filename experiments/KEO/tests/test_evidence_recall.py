"""全文召回融合的离线测试，不连接数据库和模型。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from experiments.KEO.core.graph_rag.graph_retriever import GraphRetriever
from experiments.KEO.core.graph_rag.neo4j_store import Neo4jStore
from experiments.KEO.core.graph_rag.relevance import fulltext_query
from experiments.KEO.models.graph_schema import (
    EvidenceSearchHit, GraphSubgraph, NodeSearchResult,
)
from experiments.KEO.tests.test_graph_context_ranking import node, edge


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield row
        return iterate()


class RecallTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        settings = SimpleNamespace(SEED_TOP_K=5, MAX_NODES=100, MAX_EDGES=150,
            MAX_HOPS=2, EVIDENCE_RECALL_ENABLED=True, CASE_EXPANSION_ENABLED=True,
            RETRIEVAL_MAX_CASES=1, CONTEXT_MAX_CASES=5, CASE_ANCHOR_FACTS=3,
            CASE_FACT_CANDIDATES=12)
        wrong = GraphSubgraph(nodes=[node("s", "客服"),node("w", "投资平台")],
            edges=[edge("wrong", "case_wrong", "s", "w", "投资盈利")])
        good = GraphSubgraph(nodes=[node("a", "学生账户"),node("b", "贷款额度")],
            edges=[edge("good", "case_good", "a", "b", "注销账户清空贷款额度")])
        hits = [EvidenceSearchHit(source_id="case_good",unit_id="u1",
            text="以注销学生账户为由，要求清空贷款额度。", score=2.0,triple_ids=["good"])]
        store = SimpleNamespace(settings=settings,
            get_state=AsyncMock(return_value=SimpleNamespace(status="ready",embedding_fingerprint="fp",version=4)),
            assert_query_ready=AsyncMock(), get_subgraph=AsyncMock(return_value=wrong),
            search_evidence=AsyncMock(return_value=hits),
            get_case_facts=AsyncMock(side_effect=[good, good]))
        index = SimpleNamespace(store=store, fingerprint="fp",index_key="idx",
            search=AsyncMock(return_value=[NodeSearchResult(node_id="s",score=0.9)]))
        return GraphRetriever(store,index), wrong, good, hits

    async def test_evidence_can_recover_case_missed_by_entity_route(self):
        retriever, _, _, _ = self.fixture()
        result = await retriever.retrieve("注销学生账户清空贷款额度。请判断诈骗类型。")
        self.assertEqual([e.triple_id for e in result.subgraph.edges],["good"])
        self.assertIn("s",{n.node_id for n in result.subgraph.nodes})
        self.assertEqual(result.version,4)
        self.assertEqual(retriever.store.get_subgraph.call_args.kwargs["hops"],1)
        cases = retriever.store.get_case_facts.call_args.args[0]
        self.assertEqual(cases,{"case_good":["good"]})
        self.assertTrue(retriever.store.get_case_facts.call_args.kwargs["expand"])

    async def test_evidence_route_works_without_vector_seeds(self):
        retriever, _, _, _ = self.fixture()
        retriever.node_index.search.return_value = []
        result = await retriever.retrieve("注销学生账户清空贷款额度")
        self.assertEqual([e.triple_id for e in result.subgraph.edges],["good"])
        retriever.store.get_subgraph.assert_not_awaited()

    async def test_expansion_ablation_uses_same_recalled_anchors(self):
        retriever, _, _, _ = self.fixture()
        retriever.settings.CASE_EXPANSION_ENABLED = False
        await retriever.retrieve("注销学生账户清空贷款额度")
        self.assertFalse(retriever.store.get_case_facts.call_args.kwargs["expand"])

    async def test_old_node_only_route_can_be_selected_explicitly(self):
        retriever, _, _, _ = self.fixture()
        retriever.settings.EVIDENCE_RECALL_ENABLED = False
        result = await retriever.retrieve("注销学生账户清空贷款额度")
        self.assertEqual([e.triple_id for e in result.subgraph.edges],["wrong"])
        retriever.store.search_evidence.assert_not_awaited()
        self.assertEqual(retriever.store.get_subgraph.call_args.kwargs["hops"],2)

    async def test_index_error_not_silently_downgraded(self):
        retriever, _, _, _ = self.fixture()
        retriever.store.search_evidence.side_effect = RuntimeError("index missing")
        with self.assertRaisesRegex(RuntimeError,"index missing"):
            await retriever.retrieve("注销学生账户")

    def test_wrong_source_evidence_fact_mapping_is_rejected(self):
        retriever, wrong, good, hits = self.fixture()
        hits[0].source_id = "case_other"
        with self.assertRaisesRegex(RuntimeError,"来源不一致"):
            retriever._rank_cases("注销学生账户",wrong,good,hits)

    def test_merge_excludes_unselected_case_and_honors_size(self):
        retriever, wrong, good, _ = self.fixture()
        nodes = {n.node_id:n for graph in (wrong,good) for n in graph.nodes}
        facts = {e.triple_id:e for graph in (wrong,good) for e in graph.edges}
        retriever.settings.MAX_NODES = 3
        merged = retriever._merge_final(["s"],{"case_good":["good"]},nodes,facts,wrong)
        self.assertEqual([e.triple_id for e in merged.edges],["good"])
        self.assertEqual(len(merged.nodes),3)

    def test_lucene_query_is_terms_only_and_bounded(self):
        query = fulltext_query('注销学生账户 " OR *:* 请判断诈骗类型',3)
        self.assertLessEqual(len(query.split(" OR ")),3)
        self.assertNotIn("*",query)
        self.assertNotIn(":",query)
        self.assertNotIn("诈骗",query)
        self.assertEqual(fulltext_query("123456",64),"")

    async def test_index_contract_rejects_wrong_analyzer(self):
        store = object.__new__(Neo4jStore)
        store.settings = SimpleNamespace(EVIDENCE_FULLTEXT_INDEX="idx", EVIDENCE_FULLTEXT_ANALYZER="cjk")
        tx = SimpleNamespace(run=AsyncMock(return_value=Rows([{
            "state":"ONLINE","labelsOrTypes":["KEOEvidence"],"properties":["text"],
            "options":{"indexConfig":{"fulltext.analyzer":"standard"}},
        }])))
        with self.assertRaisesRegex(RuntimeError,"定义不匹配"):
            await store._evidence_index(tx)

    async def test_search_binds_text_without_shadowing_driver_query_argument(self):
        store = object.__new__(Neo4jStore)
        store.settings = SimpleNamespace(EVIDENCE_QUERY_MAX_TERMS=64,
            EVIDENCE_FULLTEXT_INDEX="idx", EVIDENCE_SEARCH_POOL=120,
            EVIDENCE_SEARCH_TOP_K=24, EVIDENCE_HIT_MAX_CHARS=4000,
            EVIDENCE_FACTS_PER_HIT=4)
        store._load_build_ids = lambda: {"case_good"}
        store._query_state = AsyncMock()
        store._evidence_index = AsyncMock()
        captured = {}

        class Transaction:
            # 与真实驱动一致：query 是已占用的位置参数，不能重复传入。
            async def run(self, query, parameters=None, **kwparameters):
                captured.update(kwparameters)
                self.query = query
                return Rows([dict(source_id="case_good",unit_id="u1",
                    text="注销学生账户",score=1.0,triple_ids=["t1"])])

        tx = Transaction()

        async def read(work):
            return await work(tx)

        store._execute_read = read
        hits = await store.search_evidence("注销学生账户", "fp", "idx", 1)
        self.assertEqual(len(hits),1)
        self.assertIn("$text_query",tx.query)
        self.assertIn("text_query",captured)
        self.assertNotIn("query",captured)
        self.assertEqual(captured["build_ids"],["case_good"])
        self.assertEqual(store._query_state.await_count,2)


if __name__ == "__main__":
    unittest.main()
