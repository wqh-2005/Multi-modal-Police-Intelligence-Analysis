"""图谱节点向量同步与种子节点检索。"""

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os

from logging import getLogger
from pathlib import Path
from typing import Dict, List, Optional

import chromadb
from chromadb.errors import NotFoundError

from app.models.SiliconFlowEmbedding import SiliconFlowEmbedding
from experiments.KEO.core.graph_rag.neo4j_store import Neo4jStore
from experiments.KEO.models.graph_schema import (
    NodeMapping,
    NodeSearchResult,
    NodeVectorRecord,
)


logger = getLogger(__name__)


class NodeIndex:
    """管理节点描述向量及种子检索。

    一个实例绑定一个Neo4jStore和一个Chroma集合。

    同步、重建和图谱导入不能并行运行。
    store的连接生命周期由调用方管理。
    """

    def __init__(
        self,
        store: Neo4jStore,
        persist_dir: Optional[str | Path] = None,
        collection_name: Optional[str] = None,
    ):
        self.store = store
        self.settings = store.settings

        self.persist_dir = Path(
            persist_dir
            if persist_dir is not None
            else self.settings.CHROMA_PERSIST_DIR
        ).expanduser().resolve()

        self.collection_name = (
            collection_name
            if collection_name is not None
            else self.settings.CHROMA_COLLECTION
        )

        self.embedding = SiliconFlowEmbedding(
            api_key=self.settings.EMBEDDING_API_KEY,
            base_url=self.settings.EMBEDDING_BASE_URL,
            model=self.settings.EMBEDDING_MODEL,
        )

        self.top_k = self.settings.SEED_TOP_K

        self._client = None
        self._collection = None
        self._collection_id = None
        self._index_key = None
        self._operation_lock = asyncio.Lock()

        # 不包含API密钥。
        self.embedding_config = {
            "base_url": self.settings.EMBEDDING_BASE_URL.rstrip("/"),
            "model": self.settings.EMBEDDING_MODEL,
            "dimension": self.settings.EMBEDDING_DIMENSION,
            "distance": self.settings.CHROMA_DISTANCE,
            "description_version": (
                self.settings.NODE_DESCRIPTION_VERSION
            ),
        }

        self.fingerprint = self._hash_json(
            self.embedding_config
        )

    @staticmethod
    def _hash_json(value) -> str:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def get_text_hash(text: str) -> str:
        return hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()

    @property
    def index_key(self) -> str:
        if self._index_key is None:
            raise RuntimeError("Chroma尚未初始化")
        return self._index_key

    def _ensure_chroma_successful(
        self,
        create: bool = False,
    ) -> None:
        """获取并检查集合。

        查询时create=False，不自动创建缺失的集合。
        同步时才允许创建新集合。
        """

        if self._client is None:
            if not create and not self.persist_dir.is_dir():
                raise FileNotFoundError(
                    f"节点向量目录不存在：{self.persist_dir}"
                )

            if create:
                self.persist_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

            self._client = chromadb.PersistentClient(
                path=str(self.persist_dir)
            )

        self._client.heartbeat()

        try:
            collection = self._client.get_collection(
                name=self.collection_name,
                embedding_function=None,
            )
        except NotFoundError:
            if not create:
                raise RuntimeError(
                    "节点向量集合不存在，请先同步"
                )

            collection = self._client.create_collection(
                name=self.collection_name,
                embedding_function=None,
                configuration={
                    "hnsw": {
                        "space": self.settings.CHROMA_DISTANCE,
                    }
                },
                metadata={
                    "keo_fingerprint": self.fingerprint,
                    "keo_embedding_config": json.dumps(
                        self.embedding_config,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            )

        metadata = collection.metadata or {}

        if metadata.get("keo_fingerprint") != self.fingerprint:
            raise RuntimeError(
                "Chroma集合配置指纹不匹配。"
                "请使用新集合显式重建，不要修改旧集合指纹。"
            )

        expected_config = json.dumps(
            self.embedding_config,
            ensure_ascii=False,
            sort_keys=True,
        )

        if metadata.get("keo_embedding_config") != expected_config:
            raise RuntimeError("Chroma集合Embedding配置不一致")

        configuration = collection.configuration
        space = (configuration.get("hnsw") or {}).get("space")

        if space != self.settings.CHROMA_DISTANCE:
            raise RuntimeError("Chroma实际距离度量不一致")

        collection_id = str(collection.id)

        if (
            self._collection_id is not None
            and collection_id != self._collection_id
        ):
            raise RuntimeError(
                "Chroma集合已被删除或替换，请重新创建NodeIndex实例"
            )

        # 将集合实际UUID纳入身份，识别同名集合被重建的情况。
        self._index_key = self._hash_json({
            "persist_dir": os.path.normcase(str(self.persist_dir)),
            "collection_name": self.collection_name,
            "collection_id": collection_id,
        })

        self._collection_id = collection_id
        self._collection = collection

    def _build_record(
        self,
        node: NodeMapping,
    ) -> NodeVectorRecord:
        """稳定生成节点描述和元数据，不写入来源数组。"""

        parts = [
            f"名称：{node.name}",
            f"类型：{node.type}",
        ]

        if node.role:
            parts.append(f"角色：{node.role}")

        parts.extend([
            f"图谱分类：{node.graph_category}",
            f"展示名称：{node.display_name}",
        ])

        document = "；".join(parts) + "。"

        # 使用固定标量字段。
        # 空role用空字符串，避免元数据合并时留下旧role。
        metadata = {
            "node_id": node.node_id,
            "scope": node.scope,
            "name": node.name,
            "type": node.type,
            "role": node.role or "",
            "graph_category": node.graph_category,
            "display_name": node.display_name,
        }

        return NodeVectorRecord(
            node_id=node.node_id,
            document=document,
            metadata=metadata,
            description_hash=self.get_text_hash(document),
            metadata_hash=self._hash_json(metadata),
        )

    def _record_metadata(
        self,
        record: NodeVectorRecord,
    ) -> dict:
        return {
            **record.metadata,
            "description_hash": record.description_hash,
            "metadata_hash": record.metadata_hash,
            "embedding_fingerprint": self.fingerprint,
        }

    def _get_existing_records(
        self,
        node_ids: List[str],
    ) -> Dict[str, dict]:
        if not node_ids:
            return {}

        result = self._collection.get(
            ids=node_ids,
            include=["documents", "metadatas"],
        )

        return {
            node_id: {
                "document": result["documents"][index],
                "metadata": result["metadatas"][index] or {},
            }
            for index, node_id in enumerate(result["ids"])
        }

    def _validate_vectors(
        self,
        vectors,
        expected_count: int,
    ) -> List[List[float]]:
        """校验数量、维度、有限值和非零向量。"""

        if len(vectors) != expected_count:
            raise RuntimeError("Embedding返回的向量数量不一致")

        checked = []

        for vector in vectors:
            values = [float(value) for value in vector]

            if len(values) != self.settings.EMBEDDING_DIMENSION:
                raise RuntimeError(
                    "Embedding实际维度与配置不一致"
                )

            if not all(math.isfinite(value) for value in values):
                raise RuntimeError(
                    "Embedding返回了NaN或无穷值"
                )

            if not any(value != 0 for value in values):
                raise RuntimeError("Embedding返回了零向量")

            checked.append(values)

        return checked

    async def _upsert_records(
        self,
        records: List[NodeVectorRecord],
    ) -> None:
        """仅为新增或描述变化的节点计算向量。"""

        batch_size = self.settings.EMBEDDING_BATCH_SIZE

        for start in range(0, len(records), batch_size):
            batch = records[start:start + batch_size]

            vectors = await asyncio.to_thread(
                self.embedding.embed_documents,
                [record.document for record in batch],
            )

            vectors = self._validate_vectors(
                vectors,
                expected_count=len(batch),
            )

            await asyncio.to_thread(
                self._collection.upsert,
                ids=[record.node_id for record in batch],
                documents=[record.document for record in batch],
                embeddings=vectors,
                metadatas=[
                    self._record_metadata(record)
                    for record in batch
                ],
            )

    async def _sync_batch(
        self,
        tasks,
    ) -> dict:
        """执行本批Chroma操作，不在这里确认Neo4j任务。"""

        nodes = await self.store.get_nodes_by_ids(
            [task.node_id for task in tasks]
        )

        delete_ids = []
        records = []

        for task in tasks:
            node = nodes.get(task.node_id)

            if task.operation == "delete":
                if node is not None:
                    raise RuntimeError(
                        f"删除任务对应节点仍存在：{task.node_id}"
                    )
                delete_ids.append(task.node_id)
            else:
                if node is None:
                    raise RuntimeError(
                        f"更新任务对应节点不存在：{task.node_id}"
                    )
                records.append(self._build_record(node))

        existing = await asyncio.to_thread(
            self._get_existing_records,
            [record.node_id for record in records],
        )

        to_embed = []
        metadata_updates = []
        unchanged = 0

        for record in records:
            old = existing.get(record.node_id)
            expected_metadata = self._record_metadata(record)

            if (
                old is None
                or old["document"] != record.document
                or old["metadata"].get("embedding_fingerprint")
                != self.fingerprint
            ):
                to_embed.append(record)
                continue

            if any(
                old["metadata"].get(key) != value
                for key, value in expected_metadata.items()
            ):
                metadata_updates.append(record)
            else:
                unchanged += 1

        await self._upsert_records(to_embed)

        if metadata_updates:
            # 不传documents或embeddings，不触发重新Embedding。
            await asyncio.to_thread(
                self._collection.update,
                ids=[
                    record.node_id
                    for record in metadata_updates
                ],
                metadatas=[
                    self._record_metadata(record)
                    for record in metadata_updates
                ],
            )

        if delete_ids:
            await asyncio.to_thread(
                self._collection.delete,
                ids=delete_ids,
            )

        # 确认前读回本批描述和元数据。
        actual = await asyncio.to_thread(
            self._get_existing_records,
            [record.node_id for record in records],
        )

        for record in records:
            stored = actual.get(record.node_id)

            if stored is None or stored["document"] != record.document:
                raise RuntimeError("Chroma节点写入核验失败")

            if any(
                stored["metadata"].get(key) != value
                for key, value in self._record_metadata(record).items()
            ):
                raise RuntimeError("Chroma节点元数据核验失败")

        if delete_ids:
            remaining = await asyncio.to_thread(
                self._get_existing_records,
                delete_ids,
            )
            if remaining:
                raise RuntimeError("Chroma节点删除核验失败")

        return {
            "embedded": len(to_embed),
            "metadata_updated": len(metadata_updates),
            "unchanged": unchanged,
            "delete_tasks": len(delete_ids),
        }

    async def sync(
        self,
        resume: bool = False,
        rebuild: bool = False,
    ) -> dict:
        """增量同步，或显式为新集合重建。

        resume=True前必须确认旧维护进程已经停止。
        rebuild首次执行要求目标集合为空，不删除旧集合。
        """

        async with self._operation_lock:
            await asyncio.to_thread(
                self._ensure_chroma_successful,
                True,
            )

            # 新建重建任务前拒绝复用已有非空集合。
            if rebuild and not resume:
                count = await asyncio.to_thread(
                    self._collection.count
                )
                if count != 0:
                    raise RuntimeError(
                        "显式重建需要新的空集合，请更换集合名"
                    )

            if rebuild:
                version = await self.store.rebuild_vector_tasks(
                    self.fingerprint,
                    self.index_key,
                    resume=resume,
                )
            elif resume:
                version = await self.store.resume_vector_sync(
                    self.fingerprint,
                    self.index_key,
                )
            else:
                version = await self.store.begin_vector_sync(
                    self.fingerprint,
                    self.index_key,
                )

            summary = {
                "batches": 0,
                "acknowledged": 0,
                "embedded": 0,
                "metadata_updated": 0,
                "unchanged": 0,
                "delete_tasks": 0,
            }

            try:
                while True:
                    tasks = await self.store.get_vector_sync_tasks()

                    if not tasks:
                        break

                    stats = await self._sync_batch(tasks)

                    acknowledged = (
                        await self.store.acknowledge_vector_tasks(
                            tasks,
                            expected_version=version,
                        )
                    )

                    summary["batches"] += 1
                    summary["acknowledged"] += acknowledged

                    for key, value in stats.items():
                        summary[key] += value

                    logger.info(
                        "向量同步批次=%d，确认任务=%d，Embedding节点=%d",
                        summary["batches"],
                        summary["acknowledged"],
                        summary["embedded"],
                    )

                await asyncio.to_thread(
                    self._ensure_chroma_successful,
                    False,
                )

                count = await asyncio.to_thread(
                    self._collection.count
                )

                await self.store.finish_vector_sync(
                    expected_version=version,
                    vector_count=count,
                )

                summary["vector_count"] = count
                summary["status"] = "ready"
                return summary

            except BaseException as exc:
                try:
                    await self.store.mark_sync_error(
                        f"节点向量同步失败：{type(exc).__name__}"
                    )
                except Exception:
                    logger.error(
                        "同步失败状态未能写入，请检查Neo4j维护状态"
                    )
                raise

    async def search(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> List[NodeSearchResult]:
        """检索种子ID，不扩展图谱，不生成答案。"""

        if not isinstance(query, str) or not query.strip():
            raise ValueError("query不能为空")

        top_k = self.top_k if top_k is None else top_k

        if (
            isinstance(top_k, bool)
            or not isinstance(top_k, int)
            or not 1 <= top_k <= self.settings.SEED_TOP_K
        ):
            raise ValueError(
                "top_k必须为正整数且不能超过SEED_TOP_K"
            )

        async with self._operation_lock:
            await asyncio.to_thread(
                self._ensure_chroma_successful,
                False,
            )

            version = await self.store.assert_query_ready(
                self.fingerprint,
                self.index_key,
            )

            count = await asyncio.to_thread(
                self._collection.count
            )

            if count == 0:
                await self.store.assert_query_ready(
                    self.fingerprint,
                    self.index_key,
                    expected_version=version,
                )
                return []

            vector = await asyncio.to_thread(
                self.embedding.embed_query,
                query.strip(),
            )

            vectors = self._validate_vectors(
                [vector],
                expected_count=1,
            )

            result = await asyncio.to_thread(
                self._collection.query,
                query_embeddings=vectors,
                n_results=min(top_k, count),
                include=["distances", "metadatas"],
            )

            # 查询期间发生维护或版本变化，丢弃本次结果。
            await self.store.assert_query_ready(
                self.fingerprint,
                self.index_key,
                expected_version=version,
            )

            output = []

            for node_id, distance, metadata in zip(
                result["ids"][0],
                result["distances"][0],
                result["metadatas"][0],
            ):
                if (
                    metadata is None
                    or metadata.get("node_id") != node_id
                    or metadata.get("embedding_fingerprint")
                    != self.fingerprint
                ):
                    raise RuntimeError(
                        "种子节点元数据或Embedding指纹不一致"
                    )

                distance = float(distance)

                if not math.isfinite(distance):
                    raise RuntimeError("Chroma返回了无效距离")

                # cosine distance = 1 - cosine similarity。
                score = max(-1.0, min(1.0, 1.0 - distance))

                output.append(
                    NodeSearchResult(
                        node_id=node_id,
                        score=score,
                    )
                )

            return output

    async def close(self) -> None:
        """关闭Embedding HTTP客户端。

        Neo4jStore由调用方关闭。
        不调用Chroma reset或删除持久化目录。
        """

        async with self._operation_lock:
            await asyncio.to_thread(
                self.embedding.client.close
            )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="节点向量同步与种子检索"
    )
    parser.add_argument(
        "--query",
        help="检索问题；不传时执行同步",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="确认旧进程退出后恢复同步或重建",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="显式为新集合建立全量节点同步任务",
    )
    args = parser.parse_args()

    if args.query is not None and (args.resume or args.rebuild):
        parser.error("--query不能与--resume或--rebuild同时使用")

    async with Neo4jStore() as store:
        index = NodeIndex(store)

        try:
            if args.query is not None:
                results = await index.search(args.query)
                output = [
                    result.model_dump()
                    for result in results
                ]
            else:
                output = await index.sync(
                    resume=args.resume,
                    rebuild=args.rebuild,
                )

            print(
                json.dumps(
                    output,
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            await index.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(main())