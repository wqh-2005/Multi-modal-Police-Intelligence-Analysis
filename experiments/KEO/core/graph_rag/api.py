"""C图谱RAG公共入口：格式1.2输入，格式1.3输出，不生成最终答案。"""

import argparse
import asyncio
import json
import logging

from logging import getLogger
from pathlib import Path
from time import perf_counter

from pydantic import ValidationError

from experiments.KEO.configs.config import graph_rag_cfg
from experiments.KEO.core.graph_rag.context_builder import ContextBuilder
from experiments.KEO.core.graph_rag.graph_retriever import GraphRetriever
from experiments.KEO.core.graph_rag.neo4j_store import Neo4jStore
from experiments.KEO.core.graph_rag.node_index import NodeIndex
from experiments.KEO.models.graph_schema import GraphRAGQuestion


logger = getLogger(__name__)


class GraphRAG:
    """供B公共回答模块复用的异步检索入口。

    graph_path保留以兼容已有调用，正式运行不读取该文件。
    不自动导入、初始化图谱结构、同步向量或切换检索方法。
    实例应在同一个事件循环中复用，结束时调用close()。
    """

    def __init__(
        self,
        graph_path: str,
        vector_dir: str,
        split_path: str,
        collection_name: str = "keo_graph_nodes",
    ):
        self.graph_path = graph_path
        self.settings = graph_rag_cfg.model_copy(update={
            "CHROMA_PERSIST_DIR": Path(vector_dir).expanduser().resolve(),
            "SPLIT_PATH": Path(split_path).expanduser().resolve(),
            "CHROMA_COLLECTION": collection_name,
        })
        if not isinstance(collection_name, str) or not collection_name.strip():
            raise ValueError("collection_name不能为空")

        self._store = None
        self._index = None
        self._retriever = None
        self._context_builder = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def _ensure_initialized(self) -> None:
        """仅建立连接和业务对象，不改变知识库数据或状态。"""
        if self._closed:
            raise RuntimeError("GraphRAG实例已关闭，请创建新实例")
        if self._store is not None:
            return
        if not self.settings.SPLIT_PATH.is_file():
            raise FileNotFoundError("划分名单不存在")

        store = Neo4jStore(self.settings)
        index = None
        try:
            await store.connect()
            index = NodeIndex(
                store,
                persist_dir=self.settings.CHROMA_PERSIST_DIR,
                collection_name=self.settings.CHROMA_COLLECTION,
            )
            retriever = GraphRetriever(store, index)
            builder = ContextBuilder(store)
        except BaseException:
            try:
                if index is not None:
                    await index.close()
            finally:
                await store.close()
            raise

        self._store = store
        self._index = index
        self._retriever = retriever
        self._context_builder = builder

    @staticmethod
    def _response(
        question_id: str,
        status: str,
        started: float,
        context: str = "",
        evidence=None,
        error=None,
    ) -> dict:
        return {
            "question_id": question_id,
            "method": "graph_rag",
            "status": status,
            "context": context,
            "evidence": evidence if evidence is not None else [],
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
            "error": error,
        }

    async def retrieve(self, question_record: dict) -> dict:
        """输入问题字典，返回格式1.3普通字典。

        参数错误返回INVALID_INPUT，检索失败返回RETRIEVAL_ERROR。
        任务取消继续向上传播，不转换为空结果。
        无有效问题ID时错误结果回显空字符串，不编造编号；
        批量调用方应将这种输入放入独立输入错误报告。
        """
        started = perf_counter()
        question_id = ""
        if isinstance(question_record, dict):
            value = question_record.get("question_id")
            if isinstance(value, str):
                question_id = value

        try:
            if not isinstance(question_record, dict):
                raise ValueError("问题输入必须是字典")
            question = GraphRAGQuestion.model_validate(question_record)
            if (
                not question.question_id.strip()
                or question.question_id != question.question_id.strip()
                or not question.question.strip()
            ):
                raise ValueError("问题ID或问题正文无效")
        except (ValidationError, ValueError, TypeError):
            return self._response(
                question_id, "error", started,
                error={
                    "type": "INVALID_INPUT",
                    "message": "请提供有效question_id、question_type和非空question。",
                },
            )

        try:
            # 串行复用同一实例，避免连接初始化、查询和close交叉。
            async with self._lock:
                await self._ensure_initialized()
                retrieved = await self._retriever.retrieve(question.question)
                built = await self._context_builder.build(retrieved)

                # 覆盖完整公开调用流程，不能只依赖各阶段的局部校验。
                await self._store.assert_query_ready(
                    retrieved.fingerprint,
                    retrieved.index_key,
                    expected_version=retrieved.version,
                )

                if not built.context and not built.evidence:
                    return self._response(question_id, "empty", started)
                if not built.context or not built.evidence:
                    raise RuntimeError("上下文和证据不一致")
                if len(built.context) > self.settings.MAX_CONTEXT_CHARS:
                    raise RuntimeError("上下文超过配置预算")
                if len(built.evidence) > self.settings.MAX_EVIDENCE_UNITS:
                    raise RuntimeError("证据数量超过配置预算")
                ids = {item.evidence_id for item in built.evidence}
                if len(ids) != len(built.evidence):
                    raise RuntimeError("证据引用编号重复")
                if any(f"[{value}]" not in built.context for value in ids):
                    raise RuntimeError("证据引用未出现在上下文")

                return self._response(
                    question_id, "ok", started,
                    context=built.context,
                    evidence=[item.model_dump() for item in built.evidence],
                )
        except Exception as exc:
            # 不把异常原文、密钥、数据库参数或部分上下文交给外部模块。
            logger.error("图谱RAG检索失败，异常类型：%s", type(exc).__name__)
            return self._response(
                question_id, "error", started,
                error={
                    "type": "RETRIEVAL_ERROR",
                    "message": f"图谱检索失败（{type(exc).__name__}），请检查知识库状态及服务配置。",
                },
            )

    async def close(self) -> None:
        """释放HTTP客户端和Neo4j连接池，不删除持久化数据。"""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._index is not None:
                    await self._index.close()
            finally:
                if self._store is not None:
                    await self._store.close()
                self._store = None
                self._index = None
                self._retriever = None
                self._context_builder = None

    async def __aenter__(self) -> "GraphRAG":
        # 延迟连接，让服务不可用也能通过retrieve返回格式1.3错误。
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="C图谱RAG格式1.3公共接口")
    parser.add_argument("--query", required=True, help="问题正文")
    parser.add_argument("--question-id", default="q001", help="调试问题编号")
    parser.add_argument(
        "--question-type", default="case_judgment",
        choices=["global_analysis", "case_judgment"],
    )
    parser.add_argument("--graph-path", default=str(graph_rag_cfg.DEBUG_GRAPH_PATH))
    parser.add_argument("--vector-dir", default=str(graph_rag_cfg.CHROMA_PERSIST_DIR))
    parser.add_argument("--split-path", default=str(graph_rag_cfg.SPLIT_PATH))
    parser.add_argument("--collection-name", default=graph_rag_cfg.CHROMA_COLLECTION)
    args = parser.parse_args()

    async with GraphRAG(
        graph_path=args.graph_path,
        vector_dir=args.vector_dir,
        split_path=args.split_path,
        collection_name=args.collection_name,
    ) as graph_rag:
        result = await graph_rag.retrieve({
            "question_id": args.question_id,
            "question_type": args.question_type,
            "question": args.query,
        })
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
