"""C图谱RAG的Neo4j存储。"""

import asyncio
import json
import math
import re
from collections import Counter
from uuid import uuid4

from experiments.KEO.core.graph_rag.relevance import lexical_scores, query_terms
from logging import getLogger
from typing import Optional, Dict, List

from neo4j import AsyncDriver, AsyncGraphDatabase, Query, unit_of_work

from experiments.KEO.configs.config import (
    GraphRAGSettings,
    graph_rag_cfg,
)

from experiments.KEO.models.graph_schema import (
    NodeMapping,
    TripleInput,
    EntityInput,
    EvidenceRecord,
    GraphEdge,
    GraphSubgraph,
    VectorSyncTask,
)
from experiments.KEO.core.graph_rag.node_mapper import NodeMapper
from experiments.KEO.core.graph_rag.relevance import fulltext_query
from experiments.KEO.models.graph_schema import EvidenceSearchHit

from experiments.KEO.models.graph_schema import KnowledgeBaseState




logger = getLogger(__name__)


class Neo4jStore:
    """管理独立实验库的连接和存储。

    当前阶段实现：
    1. 连接与关闭。
    2. 唯一约束和索引初始化。
    3. 初始知识库状态创建与读取。
    4. 三元组批量写入与相同数据安全重跑。

    一个实例复用一个连接池。
    每次操作创建独立session，不共享session。
    """

    # 单份知识库控制记录的固定名称，不是业务编号。
    STATE_NAME = "main"

    # 保存“创建数据库唯一约束”语句的元组
    CONSTRAINT_QUERIES = (
        """
        CREATE CONSTRAINT keo_entity_node_id IF NOT EXISTS
        FOR (n:KEOEntity)
        REQUIRE n.node_id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT keo_origin_entity_id IF NOT EXISTS
        FOR (n:KEOOrigin)
        REQUIRE n.entity_id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT keo_fact_triple_id IF NOT EXISTS
        FOR (n:KEOFact)
        REQUIRE n.triple_id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT keo_evidence_source_unit IF NOT EXISTS
        FOR (n:KEOEvidence)
        REQUIRE (n.source_id, n.unit_id) IS UNIQUE
        """,
        """
        CREATE CONSTRAINT keo_vector_task_node_id IF NOT EXISTS
        FOR (n:KEOVectorSyncTask)
        REQUIRE n.node_id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT keo_state_name IF NOT EXISTS
        FOR (n:KEOKnowledgeBase)
        REQUIRE n.name IS UNIQUE
        """,
    )

    #保存“创建普通索引”语句的元组，用于加快查询，不限制字段值重复
    INDEX_QUERIES = (
        """
        CREATE INDEX keo_origin_source_id IF NOT EXISTS
        FOR (n:KEOOrigin)
        ON (n.source_id)
        """,
        """
        CREATE INDEX keo_fact_source_id IF NOT EXISTS
        FOR (n:KEOFact)
        ON (n.source_id)
        """,
        """
        CREATE INDEX keo_fact_head_entity_id IF NOT EXISTS
        FOR (n:KEOFact)
        ON (n.head_entity_id)
        """,
        """
        CREATE INDEX keo_fact_tail_entity_id IF NOT EXISTS
        FOR (n:KEOFact)
        ON (n.tail_entity_id)
        """,
    )

    def __init__(
        self,
        settings: Optional[GraphRAGSettings] = None,
    ):
        self.settings = settings or graph_rag_cfg
        self._driver: Optional[AsyncDriver] = None
        self._split_signature = None
        self._build_ids = set()
        # 运行期所有者令牌只用于维护互斥，不是业务数据编号。
        self._writer_id = uuid4().hex

    @property
    def driver(self) -> AsyncDriver:
        """取得已连接的驱动。"""

        if self._driver is None:
            raise RuntimeError(
                "Neo4jStore尚未连接，请先调用connect()"
            )

        return self._driver

    async def connect(self) -> None:
        """创建连接池并验证配置指定的数据库可以访问。"""

        if self._driver is not None:
            return

        driver = AsyncGraphDatabase.driver(
            self.settings.NEO4J_URI,
            auth=(
                self.settings.NEO4J_USER,
                self.settings.NEO4J_PASSWORD,
            ),
            max_connection_pool_size=(
                self.settings.NEO4J_POOL_SIZE
            ),
            connection_timeout=(
                self.settings.NEO4J_CONNECTION_TIMEOUT
            ),
            connection_acquisition_timeout=(
                self.settings.NEO4J_CONNECTION_TIMEOUT
            ),
        )

        try:
            await driver.verify_connectivity()

            # 显式访问配置中的数据库，避免只验证服务器连接。
            async with driver.session(
                database=self.settings.NEO4J_DATABASE
            ) as session:
                result = await session.run(
                    Query(
                        "RETURN 1 AS connected",
                        timeout=self.settings.NEO4J_QUERY_TIMEOUT,
                    )
                )
                record = await result.single(strict=True)

                if record["connected"] != 1:
                    raise RuntimeError("Neo4j连接验证失败")

        except BaseException:
            # 包括任务被取消的情况，避免连接资源泄漏。
            await driver.close()
            raise

        self._driver = driver
        logger.info("实验Neo4j连接成功")

    async def close(self) -> None:
        """关闭连接池；允许重复调用。"""

        driver = self._driver
        self._driver = None

        if driver is not None:
            await driver.close()
            logger.info("实验Neo4j连接已关闭")

    async def __aenter__(self) -> "Neo4jStore":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        await self.close()

    async def initialize_schema(self) -> None:
        """创建约束、索引及初始控制记录。

        在导入前显式调用，不在每次查询时调用。

        各语句独立提交；中途失败可以重跑。
        如果现有数据违反唯一约束，直接抛出错误，
        不删除数据，也不自动合并冲突记录。
        """

        async with self.driver.session(
            database=self.settings.NEO4J_DATABASE
        ) as session:
            for cypher in (
                *self.CONSTRAINT_QUERIES,
                *self.INDEX_QUERIES,
            ):
                result = await session.run(
                    Query(
                        cypher,
                        timeout=self.settings.NEO4J_QUERY_TIMEOUT,
                    )
                )
                await result.consume()

            # 仅首次创建时设置状态，重复初始化不覆盖ready等状态。
            result = await session.run(
                Query(
                    """
                    MERGE (s:KEOKnowledgeBase {name: $name})
                    ON CREATE SET
                        s.status = 'updating',
                        s.version = 0,
                        s.phase = 'idle',
                        s.import_complete = false
                    """,
                    timeout=self.settings.NEO4J_QUERY_TIMEOUT,
                ),
                name=self.STATE_NAME,
            )
            await result.consume()

        logger.info("实验Neo4j约束及索引初始化完成")

    @staticmethod
    def _identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{name}必须是无首尾空白的非空字符串")
        return value

    @staticmethod
    def _limit(value: int, maximum: int, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{name}必须在1至{maximum}之间")
        return value

    def _load_build_ids(self) -> set:
        """只缓存案例名单，不缓存三元组；名单变化后重新检查。"""
        path = self.settings.SPLIT_PATH.resolve()
        stat = path.stat()
        signature = (str(path), stat.st_mtime_ns, stat.st_size)
        if signature != self._split_signature:
            with path.open(encoding="utf-8-sig") as file:
                split = json.load(file)
            groups = []
            for key in ("build_ids", "eval_ids"):
                values = split.get(key)
                if not isinstance(values, list):
                    raise ValueError(f"划分名单{key}必须是列表")
                for value in values:
                    self._identifier(value, key)
                group = set(values)
                if len(group) != len(values):
                    raise ValueError(f"划分名单{key}存在重复ID")
                groups.append(group)
            if not groups[0] or groups[0] & groups[1]:
                raise ValueError("建库名单为空或与评估名单交叉")
            self._build_ids = groups[0]
            self._split_signature = signature
        return self._build_ids

    async def _execute_write(self, callback, *args):
        work = unit_of_work(timeout=self.settings.NEO4J_QUERY_TIMEOUT)(callback)
        async with self.driver.session(database=self.settings.NEO4J_DATABASE) as session:
            return await session.execute_write(work, *args)

    async def _execute_read(self, callback, *args):
        work = unit_of_work(timeout=self.settings.NEO4J_QUERY_TIMEOUT)(callback)
        async with self.driver.session(database=self.settings.NEO4J_DATABASE) as session:
            return await session.execute_read(work, *args)

    async def _lock_state(self, tx) -> dict:
        """依赖属性读写取得全局控制记录锁，不更改业务版本。"""
        result = await tx.run(
            """
            MATCH (s:KEOKnowledgeBase {name: $name})
            SET s.version = s.version + 0
            RETURN properties(s) AS state
            """, name=self.STATE_NAME,
        )
        return dict((await result.single(strict=True))["state"])

    async def _start_mutation(self, tx) -> int:
        state = await self._lock_state(tx)
        if state.get("phase", "idle") in ("sync", "reindex"):
            raise RuntimeError("向量同步或重建期间不能修改图谱")
        if state.get("phase") == "import" and state.get("owner") != self._writer_id:
            raise RuntimeError("其他导入器正在维护知识库")
        if state["status"] == "error" and not state.get("import_complete") and state.get("phase") != "import":
            raise RuntimeError("之前的导入失败，须先begin_import重新完整导入")
        result = await tx.run(
            """
            MATCH (s:KEOKnowledgeBase {name: $name})
            SET s.version = s.version + 1, s.status = 'updating',
                s.error_message = null,
                s.import_complete = CASE WHEN s.phase = 'import' THEN false ELSE true END
            RETURN s.version AS version
            """, name=self.STATE_NAME,
        )
        return (await result.single(strict=True))["version"]

    async def _queue_vector_tasks(self, tx, node_ids, operation, version):
        if not node_ids:
            return
        result = await tx.run(
            """
            UNWIND $ids AS node_id
            MERGE (task:KEOVectorSyncTask {node_id: node_id})
            SET task.operation = $operation, task.revision = $version
            """, ids=sorted(set(node_ids)), operation=operation, version=version,
        )
        await result.consume()

    async def get_state(self) -> KnowledgeBaseState:
        """读取知识库持久状态，不读取业务图谱。"""

        async with self.driver.session(
            database=self.settings.NEO4J_DATABASE
        ) as session:
            result = await session.run(
                Query(
                    """
                    MATCH (s:KEOKnowledgeBase {name: $name})
                    RETURN
                        s.status AS status,
                        s.version AS version,
                        s.embedding_fingerprint
                            AS embedding_fingerprint,
                        s.error_message AS error_message
                    """,
                    timeout=self.settings.NEO4J_QUERY_TIMEOUT,
                ),
                name=self.STATE_NAME,
            )
            record = await result.single(strict=True)

        return KnowledgeBaseState.model_validate(dict(record))

    def _prepare_batch(
        self,
        triples: List[TripleInput],
        entity_mappings: Dict[str, NodeMapping],
    ) -> tuple:
        """事务写入一批三元组。

        Args:
            triples:
                本批三元组，不接收全量数据。
            entity_mappings:
                原实体ID到NodeMapping的映射。
                由GraphBuilder调用NodeMapper后提供。

        Returns:
            本批接收的不同triple_id数量。
            包含已存在且内容一致的记录，不等于新增数量。

        注意：
            调用方必须先按build_ids过滤。
            当前方法不支持隐式覆盖已有事实。
        """

        if len(triples) > self.settings.IMPORT_BATCH_SIZE:
            raise ValueError(
                "单批三元组数量超过IMPORT_BATCH_SIZE"
            )

        nodes = {}
        origins = {}
        facts = {}
        evidences = {}
        evidence_links = {}
        build_ids = self._load_build_ids()
        mapper = NodeMapper()

        def collect(records, key, value, record_type):
            """同批同ID出现矛盾内容时拒绝写入。"""

            if key in records and records[key] != value:
                raise ValueError(
                    f"{record_type}内容冲突：{key}"
                )

            records[key] = value

        for item in triples:
            if not isinstance(item, TripleInput):
                raise TypeError(
                    "triples中的元素必须是TripleInput"
                )

            self._identifier(item.triple_id, "triple_id")
            self._identifier(item.source_id, "source_id")
            if item.source_id not in build_ids:
                raise ValueError(f"拒绝写入非build_ids来源：{item.source_id}")
            for field in ("relation", "factuality", "source_type", "fraud_category"):
                if not getattr(item, field).strip():
                    raise ValueError(f"{field}不能为空白")
            if item.confidence is not None and not math.isfinite(item.confidence):
                raise ValueError("confidence必须是有限数值")

            mapped_nodes = []

            for entity in (item.head, item.tail):
                self._identifier(entity.id, "entity.id")
                mapping = entity_mappings.get(entity.id)

                if mapping is None:
                    raise ValueError(
                        f"缺少原实体映射：{entity.id}"
                    )

                if not isinstance(mapping, NodeMapping):
                    raise TypeError(
                        "entity_mappings的值必须是NodeMapping"
                    )
                if mapping != mapper.map_entity(entity.model_dump(), item.source_id):
                    raise ValueError(f"映射不符合当前NodeMapper规则：{entity.id}")

                if (
                    mapping.scope == "case"
                    and mapping.node_id != entity.id
                ):
                    raise ValueError(
                        f"局部节点ID必须沿用原实体ID：{entity.id}"
                    )

                if (
                    entity.type.strip() == "人员"
                    and mapping.scope != "case"
                ):
                    raise ValueError(
                        f"人员不能映射为共享节点：{entity.id}"
                    )

                collect(
                    nodes,
                    mapping.node_id,
                    mapping.model_dump(),
                    "图谱节点",
                )

                origin = {
                    "entity_id": entity.id,
                    "source_id": item.source_id,
                    "name": entity.name,
                    "type": entity.type,
                    "role": entity.role,
                    "node_id": mapping.node_id,
                }

                collect(
                    origins,
                    entity.id,
                    origin,
                    "原实体",
                )

                mapped_nodes.append(mapping)

            head_node, tail_node = mapped_nodes

            fact = {
                "triple_id": item.triple_id,
                "source_id": item.source_id,
                "source_type": item.source_type,
                "fraud_category": item.fraud_category,
                "head_entity_id": item.head.id,
                "tail_entity_id": item.tail.id,
                "relation": item.relation,
                "factuality": item.factuality,
                "confidence": item.confidence,
                "status": item.status,
            }

            # 图谱端点只用于建立连接，不写入事实属性。
            fact_row = {
                "properties": fact,
                "head_node_id": head_node.node_id,
                "tail_node_id": tail_node.node_id,
            }

            collect(
                facts,
                item.triple_id,
                fact_row,
                "三元组",
            )

            unit_ids = set()

            for evidence in item.evidence:
                self._identifier(evidence.unit_id, "unit_id")
                if not evidence.text.strip():
                    raise ValueError("证据text不能为空白")
                key = (item.source_id, evidence.unit_id)

                collect(
                    evidences,
                    key,
                    {
                        "source_id": item.source_id,
                        "unit_id": evidence.unit_id,
                        "text": evidence.text,
                    },
                    "证据",
                )

                unit_ids.add(evidence.unit_id)

            collect(
                evidence_links,
                item.triple_id,
                sorted(unit_ids),
                "三元组证据关联",
            )

        links = [
            {
                "triple_id": triple_id,
                "source_id": facts[triple_id][
                    "properties"
                ]["source_id"],
                "unit_ids": unit_ids,
            }
            for triple_id, unit_ids in evidence_links.items()
        ]

        return (
            list(nodes.values()), list(origins.values()), list(facts.values()),
            list(evidences.values()), links,
        )

    async def write_batch(
        self, triples: List[TripleInput], entity_mappings: Dict[str, NodeMapping],
    ) -> int:
        """新增或核验相同记录；内容变化必须显式调用update_batch。"""
        if not triples:
            return 0
        rows = self._prepare_batch(triples, entity_mappings)
        await self._execute_write(self._write_batch_tx, *rows)
        return len(rows[2])

    async def _write_batch_tx(
        self,
        tx,
        nodes,
        origins,
        facts,
        evidences,
        links,
    ) -> None:
        """单批事务；检查和写入必须全部成功。

        execute_write可能重试此方法，
        因此这里不调用Embedding或写外部文件。
        """

        async def run(cypher, **parameters):
            return await tx.run(
                cypher,
                **parameters,
            )

        async def consume(cypher, **parameters):
            result = await run(cypher, **parameters)
            await result.consume()

        def check_properties(existing, expected, label):
            """仅比较本模块管理的字段，null对应属性缺失。"""

            for key, value in expected.items():
                if existing.get(key) != value:
                    raise ValueError(
                        f"{label}已有内容冲突，"
                        f"字段={key}，请使用显式更新操作"
                    )

        # 先取得控制记录的写锁，串行化本存储类的批次写入。
        # 与后续业务写入在同一事务提交。
        version = await self._start_mutation(tx)

        # ---------- 校验已有图谱节点 ----------

        result = await run(
            """
            UNWIND $rows AS row
            MATCH (n:KEOEntity {node_id: row.node_id})
            RETURN
                row.node_id AS node_id,
                properties(n) AS properties
            """,
            rows=nodes,
        )
        expected_nodes = {
            row["node_id"]: row for row in nodes
        }
        dirty_nodes = set(expected_nodes)

        async for record in result:
            check_properties(
                record["properties"],
                expected_nodes[record["node_id"]],
                "图谱节点",
            )
            if record["properties"].get("synced_fingerprint"):
                dirty_nodes.discard(record["node_id"])

        # ---------- 校验已有原实体及映射 ----------

        result = await run(
            """
            UNWIND $rows AS row
            MATCH (o:KEOOrigin {entity_id: row.entity_id})
            OPTIONAL MATCH (o)-[:MAPS_TO]->(n:KEOEntity)
            RETURN
                row.entity_id AS entity_id,
                properties(o) AS properties,
                collect(n.node_id) AS node_ids
            """,
            rows=origins,
        )
        expected_origins = {
            row["entity_id"]: row for row in origins
        }

        async for record in result:
            row = expected_origins[record["entity_id"]]

            check_properties(
                record["properties"],
                {
                    key: value
                    for key, value in row.items()
                    if key != "node_id"
                },
                "原实体",
            )

            if record["node_ids"] != [row["node_id"]]:
                raise ValueError(
                    f"原实体映射冲突：{row['entity_id']}"
                )

        # ---------- 校验已有事实和端点 ----------

        result = await run(
            """
            UNWIND $rows AS row
            MATCH (f:KEOFact {
                triple_id: row.properties.triple_id
            })
            OPTIONAL MATCH (f)-[:HEAD]->(h:KEOEntity)
            WITH row, f, collect(h.node_id) AS heads
            OPTIONAL MATCH (f)-[:TAIL]->(t:KEOEntity)
            RETURN
                row.properties.triple_id AS triple_id,
                properties(f) AS properties,
                heads,
                collect(t.node_id) AS tails
            """,
            rows=facts,
        )
        expected_facts = {
            row["properties"]["triple_id"]: row
            for row in facts
        }
        existing_fact_ids = set()

        async for record in result:
            triple_id = record["triple_id"]
            row = expected_facts[triple_id]

            check_properties(
                record["properties"],
                row["properties"],
                "事实",
            )

            if (
                record["heads"] != [row["head_node_id"]]
                or record["tails"] != [row["tail_node_id"]]
            ):
                raise ValueError(
                    f"事实端点冲突：{triple_id}"
                )

            existing_fact_ids.add(triple_id)

        # ---------- 校验已有证据文本 ----------

        result = await run(
            """
            UNWIND $rows AS row
            MATCH (e:KEOEvidence {
                source_id: row.source_id,
                unit_id: row.unit_id
            })
            RETURN
                row.source_id AS source_id,
                row.unit_id AS unit_id,
                e.text AS text
            """,
            rows=evidences,
        )
        expected_evidence = {
            (row["source_id"], row["unit_id"]): row["text"]
            for row in evidences
        }

        async for record in result:
            key = (
                record["source_id"],
                record["unit_id"],
            )

            if record["text"] != expected_evidence[key]:
                raise ValueError(
                    f"证据文本冲突：{key}"
                )

        # 已有事实必须保持原证据集合，不能重跑时默默补写。
        existing_links = [
            row for row in links
            if row["triple_id"] in existing_fact_ids
        ]

        result = await run(
            """
            UNWIND $rows AS row
            MATCH (f:KEOFact {triple_id: row.triple_id})
            OPTIONAL MATCH
                (f)-[:SUPPORTED_BY]->(e:KEOEvidence)
            RETURN
                row.triple_id AS triple_id,
                collect({
                    source_id: e.source_id,
                    unit_id: e.unit_id
                }) AS evidence_keys
            """,
            rows=existing_links,
        )
        expected_links = {
            row["triple_id"]: {
                (row["source_id"], unit_id)
                for unit_id in row["unit_ids"]
            }
            for row in existing_links
        }

        async for record in result:
            actual_keys = {
                (item["source_id"], item["unit_id"])
                for item in record["evidence_keys"]
                if item["unit_id"] is not None
            }

            if actual_keys != expected_links[record["triple_id"]]:
                raise ValueError(
                    f"事实证据关联冲突：{record['triple_id']}"
                )

        # ---------- 写入节点，同时创建向量同步任务 ----------

        await consume(
            """
            UNWIND $rows AS row
            MERGE (n:KEOEntity {node_id: row.node_id})
            ON CREATE SET n += row
            """,
            rows=nodes,
        )

        # 无变化且已经同步的节点不重新排队；已有待处理任务保持不变。
        await self._queue_vector_tasks(tx, dirty_nodes, "upsert", version)

        await consume(
            """
            UNWIND $rows AS row
            MATCH (n:KEOEntity {node_id: row.node_id})
            MERGE (o:KEOOrigin {entity_id: row.entity_id})
            ON CREATE SET
                o.source_id = row.source_id,
                o.name = row.name,
                o.type = row.type,
                o.role = row.role
            MERGE (o)-[:MAPS_TO]->(n)
            """,
            rows=origins,
        )

        await consume(
            """
            UNWIND $rows AS row
            MERGE (e:KEOEvidence {
                source_id: row.source_id,
                unit_id: row.unit_id
            })
            ON CREATE SET e.text = row.text
            """,
            rows=evidences,
        )

        await consume(
            """
            UNWIND $rows AS row
            MATCH (h:KEOEntity {
                node_id: row.head_node_id
            })
            MATCH (t:KEOEntity {
                node_id: row.tail_node_id
            })
            MERGE (f:KEOFact {
                triple_id: row.properties.triple_id
            })
            ON CREATE SET f += row.properties
            MERGE (f)-[:HEAD]->(h)
            MERGE (f)-[:TAIL]->(t)
            """,
            rows=facts,
        )

        await consume(
            """
            UNWIND $rows AS row
            MATCH (f:KEOFact {triple_id: row.triple_id})
            UNWIND row.unit_ids AS unit_id
            MATCH (e:KEOEvidence {
                source_id: row.source_id,
                unit_id: unit_id
            })
            MERGE (f)-[:SUPPORTED_BY]->(e)
            """,
            rows=links,
        )

    async def _read_fact_rows(self, tx, triple_ids):
        result = await tx.run(
            """
            UNWIND $ids AS triple_id
            MATCH (f:KEOFact {triple_id: triple_id})
            CALL { WITH f OPTIONAL MATCH (f)-[:HEAD]->(h:KEOEntity)
                   RETURN collect(h.node_id) AS heads }
            CALL { WITH f OPTIONAL MATCH (f)-[:TAIL]->(t:KEOEntity)
                   RETURN collect(t.node_id) AS tails }
            CALL { WITH f OPTIONAL MATCH (f)-[:SUPPORTED_BY]->(e:KEOEvidence)
                   RETURN collect(e { .source_id, .unit_id, .text }) AS evidence }
            RETURN properties(f) AS properties, heads, tails, evidence
            """, ids=triple_ids,
        )
        return await result.data()

    async def _remove_facts_tx(self, tx, triple_ids):
        rows = await self._read_fact_rows(tx, triple_ids)
        result = await tx.run(
            """
            UNWIND $ids AS triple_id
            MATCH (f:KEOFact {triple_id: triple_id})
            DETACH DELETE f
            """, ids=triple_ids,
        )
        await result.consume()
        return rows

    async def _cleanup_tx(self, tx, old_rows, version, extra_nodes=()):
        """仅检查本次变更影响的对象，绝不扫描并清理全库。"""
        origins, node_ids, evidence_keys = set(), set(extra_nodes), set()
        for row in old_rows:
            props = row["properties"]
            origins.update((props["head_entity_id"], props["tail_entity_id"]))
            node_ids.update(row["heads"] + row["tails"])
            evidence_keys.update(
                (item["source_id"], item["unit_id"]) for item in row["evidence"]
            )
        result = await tx.run(
            """
            UNWIND $ids AS entity_id
            MATCH (o:KEOOrigin {entity_id: entity_id})
            WHERE NOT EXISTS { MATCH (f:KEOFact) WHERE f.head_entity_id = entity_id }
              AND NOT EXISTS { MATCH (f:KEOFact) WHERE f.tail_entity_id = entity_id }
              AND NOT EXISTS { MATCH (o)-[r]-() WHERE type(r) <> 'MAPS_TO' }
            OPTIONAL MATCH (o)-[:MAPS_TO]->(n:KEOEntity)
            WITH o, collect(n.node_id) AS nodes
            DETACH DELETE o
            RETURN nodes
            """, ids=sorted(origins),
        )
        async for record in result:
            node_ids.update(record["nodes"])
        result = await tx.run(
            """
            UNWIND $keys AS key
            MATCH (e:KEOEvidence {source_id: key.source_id, unit_id: key.unit_id})
            WHERE NOT EXISTS { MATCH (e)--() }
            DELETE e
            """, keys=[{"source_id": sid, "unit_id": uid} for sid, uid in sorted(evidence_keys)],
        )
        await result.consume()
        # 任意关系（包括其他来源映射）仍存在时都不删除共享节点。
        result = await tx.run(
            """
            UNWIND $ids AS node_id
            MATCH (n:KEOEntity {node_id: node_id})
            WHERE NOT EXISTS { MATCH (n)--() }
            RETURN n.node_id AS node_id
            """, ids=sorted(node_ids),
        )
        deleted = [record["node_id"] async for record in result]
        await self._queue_vector_tasks(tx, deleted, "delete", version)
        result = await tx.run(
            """
            UNWIND $ids AS node_id
            MATCH (n:KEOEntity {node_id: node_id})
            DELETE n
            """, ids=deleted,
        )
        await result.consume()

    async def update_batch(self, triples, entity_mappings) -> int:
        """显式替换已有事实；原实体修正应先调用update_entity。

        原source_id不可改变。证据文本冲突也不会隐式覆盖。
        单批替换、引用清理与向量任务在一个事务中提交。
        """
        if not triples:
            return 0
        rows = self._prepare_batch(triples, entity_mappings)
        ids = [row["properties"]["triple_id"] for row in rows[2]]

        async def update(tx):
            await self._start_mutation(tx)
            old = await self._read_fact_rows(tx, ids)
            existing = {row["properties"]["triple_id"]: row for row in old}
            for row in rows[2]:
                props = row["properties"]
                previous = existing.get(props["triple_id"])
                if previous is None or previous["properties"]["source_id"] != props["source_id"]:
                    raise ValueError("更新的事实不存在或source_id发生变化")
            await self._remove_facts_tx(tx, ids)
            await self._write_batch_tx(tx, *rows)
            state = await self._lock_state(tx)
            await self._cleanup_tx(tx, old, state["version"])
            return len(ids)

        return await self._execute_write(update)

    async def delete_triples(self, triple_ids: List[str]) -> int:
        """显式删除有限数量的事实，不存在的ID按幂等删除处理。"""
        if not triple_ids:
            return 0
        self._limit(len(triple_ids), self.settings.DELETE_BATCH_SIZE, "删除数量")
        ids = sorted({self._identifier(value, "triple_id") for value in triple_ids})

        async def delete(tx):
            version = await self._start_mutation(tx)
            old = await self._remove_facts_tx(tx, ids)
            await self._cleanup_tx(tx, old, version)
            return len(old)

        return await self._execute_write(delete)

    async def delete_source(self, source_id: str) -> int:
        """按来源分批删除，可重跑；整个来源不是一个大事务。

        允许删除已移出build_ids的旧来源，用于显式清理。
        """
        self._identifier(source_id, "source_id")
        total = 0
        while True:
            async def delete(tx):
                version = await self._start_mutation(tx)
                result = await tx.run(
                    """
                    MATCH (f:KEOFact {source_id: $source_id})
                    RETURN f.triple_id AS triple_id LIMIT $limit
                    """, source_id=source_id, limit=self.settings.DELETE_BATCH_SIZE,
                )
                ids = [record["triple_id"] async for record in result]
                old = await self._remove_facts_tx(tx, ids)
                await self._cleanup_tx(tx, old, version)
                return len(old)
            count = await self._execute_write(delete)
            total += count
            if count == 0:
                return total

    async def update_entity(self, source_id: str, entity: EntityInput) -> NodeMapping:
        """显式修正一个原实体，并迁移所有引用它的事实端点。

        单个实体引用量超过IMPORT_BATCH_SIZE时拒绝本次事务，
        不进行半份迁移；这种异常高引用实体需独立迁移方案。
        """
        self._identifier(source_id, "source_id")
        if source_id not in self._load_build_ids():
            raise ValueError("不能修正非建库来源的实体")
        if not isinstance(entity, EntityInput):
            raise TypeError("entity必须是EntityInput")
        self._identifier(entity.id, "entity.id")
        mapping = NodeMapper().map_entity(entity.model_dump(), source_id)

        async def update(tx):
            version = await self._start_mutation(tx)
            result = await tx.run(
                """
                MATCH (o:KEOOrigin {entity_id: $id})
                OPTIONAL MATCH (o)-[:MAPS_TO]->(n:KEOEntity)
                RETURN o.source_id AS source_id, collect(n.node_id) AS nodes
                """, id=entity.id,
            )
            origin = await result.single(strict=True)
            if origin["source_id"] != source_id or len(origin["nodes"]) != 1:
                raise ValueError("原实体来源或已有映射不一致")
            old_node = origin["nodes"][0]
            fact_ids = set()
            for field in ("head_entity_id", "tail_entity_id"):
                # field来自固定白名单，不拼接输入内容。
                result = await tx.run(
                    f"MATCH (f:KEOFact) WHERE f.{field} = $id "
                    "RETURN f.triple_id AS id, f.source_id AS source_id LIMIT $limit",
                    id=entity.id, limit=self.settings.IMPORT_BATCH_SIZE + 1,
                )
                async for record in result:
                    if record["source_id"] != source_id:
                        raise ValueError("同一原实体被其他来源事实引用")
                    fact_ids.add(record["id"])
            if len(fact_ids) > self.settings.IMPORT_BATCH_SIZE:
                raise ValueError("原实体引用量超过单批上限，未执行迁移")
            result = await tx.run(
                """
                MATCH (n:KEOEntity {node_id: $id})
                RETURN properties(n) AS properties,
                    EXISTS { MATCH (o:KEOOrigin)-[:MAPS_TO]->(n)
                             WHERE o.entity_id <> $entity_id } AS shared
                """, id=mapping.node_id, entity_id=entity.id,
            )
            target = await result.single()
            if target is not None:
                different = any(target["properties"].get(k) != v for k, v in mapping.model_dump().items())
                if different and (mapping.node_id != old_node or target["shared"]):
                    raise ValueError("目标共享节点属性冲突，不能覆盖其他原实体的概念")
            result = await tx.run(
                """
                MERGE (n:KEOEntity {node_id: $node.node_id})
                SET n += $node
                WITH n
                MATCH (o:KEOOrigin {entity_id: $entity.id})
                SET o.name = $entity.name, o.type = $entity.type, o.role = $entity.role
                WITH o, n
                MATCH (o)-[r:MAPS_TO]->(:KEOEntity)
                DELETE r
                MERGE (o)-[:MAPS_TO]->(n)
                """, node=mapping.model_dump(), entity=entity.model_dump(),
            )
            await result.consume()
            for field, relation in (("head_entity_id", "HEAD"), ("tail_entity_id", "TAIL")):
                result = await tx.run(
                    f"MATCH (f:KEOFact) WHERE f.{field} = $entity_id "
                    f"MATCH (f)-[r:{relation}]->(:KEOEntity) "
                    "MATCH (n:KEOEntity {node_id: $node_id}) "
                    f"DELETE r MERGE (f)-[:{relation}]->(n)",
                    entity_id=entity.id, node_id=mapping.node_id,
                )
                await result.consume()
            await self._queue_vector_tasks(tx, [mapping.node_id], "upsert", version)
            await self._cleanup_tx(tx, [], version, [old_node])
            return mapping

        return await self._execute_write(update)

    async def update_evidence(self, evidence: EvidenceRecord) -> None:
        """显式更正证据单元，所有引用该单元的事实共同生效。"""
        if not isinstance(evidence, EvidenceRecord):
            raise TypeError("evidence必须是EvidenceRecord")
        self._identifier(evidence.source_id, "source_id")
        self._identifier(evidence.unit_id, "unit_id")
        if evidence.source_id not in self._load_build_ids() or not evidence.text.strip():
            raise ValueError("证据必须属于build_ids且text非空白")

        async def update(tx):
            await self._start_mutation(tx)
            result = await tx.run(
                """
                MATCH (e:KEOEvidence {source_id: $source_id, unit_id: $unit_id})
                SET e.text = $text RETURN e.unit_id AS unit_id
                """, **evidence.model_dump(),
            )
            await result.single(strict=True)
        await self._execute_write(update)

    async def begin_import(self, resume: bool = False) -> None:
        """开始导入，阻止后续查询使用更新中的知识库。

        当前采用单写入流程，不允许同时运行多个导入或同步任务。
        """

        async def begin(tx):
            state = await self._lock_state(tx)
            phase = state.get("phase", "idle")
            if phase in ("sync", "reindex"):
                raise RuntimeError("请先结束或中止当前向量同步")
            if phase == "import" and state.get("owner") != self._writer_id and not resume:
                raise RuntimeError("已有导入任务；确认旧进程退出后才能resume=True")
            result = await tx.run(
                """
                MATCH (s:KEOKnowledgeBase {name: $name})
                SET s.version = s.version + 1, s.status = 'updating',
                    s.phase = 'import', s.owner = $owner,
                    s.import_complete = false, s.error_message = null
                """, name=self.STATE_NAME, owner=self._writer_id,
            )
            await result.consume()
        await self._execute_write(begin)

    async def finish_import(self) -> None:
        """只标记文件导入完成，仍不开放检索。"""
        async def finish(tx):
            state = await self._lock_state(tx)
            if state.get("phase") != "import" or state.get("owner") != self._writer_id:
                raise RuntimeError("只能结束本实例拥有的导入任务")
            result = await tx.run(
                """
                MATCH (s:KEOKnowledgeBase {name: $name})
                SET s.phase = 'idle', s.owner = null,
                    s.import_complete = true, s.status = 'updating'
                """, name=self.STATE_NAME,
            )
            await result.consume()
        await self._execute_write(finish)

    async def mark_import_error(self, message: str) -> None:
        """记录导入失败，不删除已提交的数据。"""

        await self._mark_maintenance_error(message, "import")

    async def _mark_maintenance_error(self, message, phase):
        async def mark(tx):
            state = await self._lock_state(tx)
            if state.get("phase") != phase or state.get("owner") != self._writer_id:
                raise RuntimeError("不能修改其他维护任务的状态")
            result = await tx.run(
                """
                MATCH (s:KEOKnowledgeBase {name: $name})
                SET s.status = 'error', s.phase = 'idle', s.owner = null,
                    s.error_message = $message
                """, name=self.STATE_NAME, message=str(message)[:1000],
            )
            await result.consume()
        await self._execute_write(mark)

    async def begin_vector_sync(
        self, fingerprint: str, index_key: str, resume: bool = False,
    ) -> int:
        """绑定Embedding指纹及实际Chroma集合身份，返回本轮图谱版本。

        index_key应包含持久化目录与集合名；不能只传模型名。
        配置变化不能在旧集合混写，使用rebuild_vector_tasks。
        """
        self._identifier(fingerprint, "fingerprint")
        self._identifier(index_key, "index_key")

        async def begin(tx):
            state = await self._lock_state(tx)
            if state.get("phase") in ("import", "reindex") or not state.get("import_complete", False):
                raise RuntimeError("导入尚未完成，不能同步或开放检索")
            if state["status"] == "error":
                raise RuntimeError("先修复失败的导入，或显式重建向量任务")
            if state.get("phase") == "sync" and state.get("owner") != self._writer_id and not resume:
                raise RuntimeError("已有同步任务；确认旧进程退出后才能resume=True")
            for key, value in (("embedding_fingerprint", fingerprint), ("index_key", index_key),
                               ("target_fingerprint", fingerprint), ("target_index_key", index_key)):
                if state.get(key) is not None and state[key] != value:
                    raise ValueError("Embedding或集合配置变化，请显式重建到新集合")
            result = await tx.run(
                """
                MATCH (s:KEOKnowledgeBase {name: $name})
                SET s.version = s.version + 1, s.status = 'updating',
                    s.phase = 'sync', s.owner = $owner,
                    s.target_fingerprint = $fingerprint,
                    s.target_index_key = $index_key, s.error_message = null
                RETURN s.version AS version
                """, name=self.STATE_NAME, owner=self._writer_id,
                fingerprint=fingerprint, index_key=index_key,
            )
            return (await result.single(strict=True))["version"]
        return await self._execute_write(begin)

    async def get_vector_sync_tasks(self, limit: Optional[int] = None) -> List[VectorSyncTask]:
        """读取有限数量的待同步任务，不使用OFFSET分页。

        成功同步并确认删除本批任务后，再读取下一批。
        读取任务不会删除任务，也不会将知识库标记为ready。
        """
        limit = self.settings.VECTOR_SYNC_BATCH_SIZE if limit is None else limit
        self._limit(limit, self.settings.VECTOR_SYNC_BATCH_SIZE, "limit")

        async def read(tx):
            result = await tx.run(
                """
                MATCH (task:KEOVectorSyncTask)
                RETURN task.node_id AS node_id, task.operation AS operation,
                    task.revision AS revision
                ORDER BY task.node_id LIMIT $limit
                """, limit=limit,
            )
            return [VectorSyncTask.model_validate(dict(row)) async for row in result]
        return await self._execute_read(read)

    async def get_nodes_by_ids(self, node_ids: List[str]) -> Dict[str, NodeMapping]:
        """按ID读取有限节点，仅返回NodeMapping需要的字段。

        同时供节点向量同步和有限子图检索使用。
        不存在的节点不出现在结果中，由调用方结合任务类型处理。
        """
        if not node_ids:
            return {}
        self._limit(len(node_ids), max(self.settings.VECTOR_SYNC_BATCH_SIZE, self.settings.MAX_NODES), "节点数量")
        ids = sorted({self._identifier(value, "node_id") for value in node_ids})

        async def read(tx):
            result = await tx.run(
                """
                UNWIND $ids AS id
                MATCH (n:KEOEntity {node_id: id})
                RETURN n {
                    .node_id, .scope, .name, .type, .role,
                    .graph_category, .display_name
                } AS node
                """,
                ids=ids,
            )
            return {row["node"]["node_id"]: NodeMapping.model_validate(row["node"]) async for row in result}
        return await self._execute_read(read)

    def _check_sync_owner(self, state, expected_version):
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("expected_version必须是非负整数")
        if (state["status"] != "updating" or state.get("phase") != "sync"
                or state.get("owner") != self._writer_id or state["version"] != expected_version):
            raise RuntimeError("同步所有者、知识库状态或版本已变化")

    async def acknowledge_vector_tasks(
        self, tasks: List[VectorSyncTask], expected_version: int,
    ) -> int:
        """仅在Chroma写入/元数据更新/删除成功后调用。

        确认upsert同时保存已同步指纹；摘要由Chroma记录维护。
        所有者、知识库版本、revision及operation共同防止确认旧任务。
        任一任务已变化时抛出异常，整个确认事务回滚。
        """
        if not tasks:
            return 0
        self._limit(len(tasks), self.settings.VECTOR_SYNC_BATCH_SIZE, "任务数量")
        if any(not isinstance(task, VectorSyncTask) for task in tasks):
            raise TypeError("tasks必须包含VectorSyncTask")
        if len({task.node_id for task in tasks}) != len(tasks):
            raise ValueError("确认列表包含重复任务")

        async def acknowledge(tx):
            state = await self._lock_state(tx)
            self._check_sync_owner(state, expected_version)
            rows = [task.model_dump() for task in tasks]
            result = await tx.run(
                """
                UNWIND $rows AS row
                MATCH (task:KEOVectorSyncTask {node_id: row.node_id})
                WHERE task.revision = row.revision AND task.operation = row.operation
                OPTIONAL MATCH (n:KEOEntity {node_id: row.node_id})
                RETURN row.node_id AS id, row.operation AS operation,
                    n IS NOT NULL AS node_exists
                """, rows=rows,
            )
            valid = await result.data()
            if len(valid) != len(rows) or any(
                row["node_exists"] != (row["operation"] == "upsert") for row in valid
            ):
                raise RuntimeError("任务已变化或节点存在状态不符，本批确认回滚")
            result = await tx.run(
                """
                UNWIND $rows AS row
                MATCH (task:KEOVectorSyncTask {node_id: row.node_id})
                WHERE task.revision = row.revision AND task.operation = row.operation
                OPTIONAL MATCH (n:KEOEntity {node_id: row.node_id})
                FOREACH (_ IN CASE WHEN n IS NULL THEN [] ELSE [1] END |
                    SET n.synced_fingerprint = $fingerprint, n.synced_index_key = $index_key)
                DELETE task
                RETURN count(*) AS acknowledged
                """, rows=rows, fingerprint=state["target_fingerprint"], index_key=state["target_index_key"],
            )
            acknowledged = (await result.single(strict=True))["acknowledged"]
            if acknowledged != len(rows):
                raise RuntimeError("部分同步任务已变化，本批确认回滚")
            return acknowledged
        return await self._execute_write(acknowledge)

    async def finish_vector_sync(self, expected_version: int, vector_count: int) -> None:
        """调用方先核验Chroma指纹及count，再传入实际记录数。

        空任务队列、逐节点指纹、集合身份、数量和建库来源均通过后才ready。
        """
        if isinstance(vector_count, bool) or not isinstance(vector_count, int) or vector_count < 0:
            raise ValueError("vector_count必须是非负整数")
        build_ids = sorted(self._load_build_ids())

        async def finish(tx):
            state = await self._lock_state(tx)
            self._check_sync_owner(state, expected_version)
            result = await tx.run(
                "MATCH (task:KEOVectorSyncTask) RETURN task.node_id AS id LIMIT 1"
            )
            if await result.single() is not None:
                raise RuntimeError("向量任务尚未全部确认")
            result = await tx.run(
                """
                MATCH (n:KEOEntity)
                WHERE n.synced_fingerprint IS NULL OR n.synced_index_key IS NULL
                   OR n.synced_fingerprint <> $fingerprint OR n.synced_index_key <> $index_key
                RETURN n.node_id AS id LIMIT 1
                """, fingerprint=state["target_fingerprint"], index_key=state["target_index_key"],
            )
            if await result.single() is not None:
                raise RuntimeError("仍有节点未同步至目标向量空间")
            for label in ("KEOFact", "KEOOrigin", "KEOEvidence"):
                result = await tx.run(
                    f"MATCH (n:{label}) WHERE n.source_id IS NULL OR NOT n.source_id IN $ids "
                    "RETURN n.source_id AS source_id LIMIT 1", ids=build_ids,
                )
                if await result.single() is not None:
                    raise RuntimeError("存在不属于当前build_ids的数据，请显式清理旧来源")
            result = await tx.run("MATCH (n:KEOEntity) RETURN count(n) AS total")
            if (await result.single(strict=True))["total"] != vector_count:
                raise RuntimeError("Chroma记录数与图谱节点数不一致")
            result = await tx.run(
                """
                MATCH (s:KEOKnowledgeBase {name: $name})
                SET s.status = 'ready', s.phase = 'idle', s.owner = null,
                    s.embedding_fingerprint = s.target_fingerprint,
                    s.index_key = s.target_index_key, s.error_message = null
                """, name=self.STATE_NAME,
            )
            await result.consume()
        await self._execute_write(finish)

    async def mark_sync_error(self, message: str) -> None:
        """任务保留，禁止查询；后续用resume_vector_sync恢复。"""
        await self._mark_maintenance_error(message, "sync")

    async def resume_vector_sync(self, fingerprint: str, index_key: str) -> int:
        """确认旧同步进程停止后恢复；不接受失败导入遗留的状态。"""
        async def reset(tx):
            state = await self._lock_state(tx)
            if not state.get("import_complete") or state.get("phase") in ("import", "reindex"):
                raise RuntimeError("不能绕过未完成的导入或索引重建")
            if state.get("target_fingerprint") != fingerprint or state.get("target_index_key") != index_key:
                raise ValueError("恢复配置与原同步目标不一致")
            result = await tx.run(
                "MATCH (s:KEOKnowledgeBase {name: $name}) SET s.status = 'updating'",
                name=self.STATE_NAME,
            )
            await result.consume()
        await self._execute_write(reset)
        return await self.begin_vector_sync(fingerprint, index_key, resume=True)

    async def rebuild_vector_tasks(
        self, fingerprint: str, index_key: str, resume: bool = False,
    ) -> int:
        """分页为全体节点排队；不读取全图，也不计算向量。

        向量空间变化必须使用新的空Chroma集合身份。
        游标持久化，进程退出后可显式resume；完成后进入sync阶段。
        """
        self._identifier(fingerprint, "fingerprint")
        self._identifier(index_key, "index_key")

        async def begin(tx):
            state = await self._lock_state(tx)
            if not state.get("import_complete") or state.get("phase") == "import":
                raise RuntimeError("先完成导入，不能绕过失败导入进行重建")
            if state.get("phase") == "reindex":
                if not resume or state.get("target_fingerprint") != fingerprint or state.get("target_index_key") != index_key:
                    raise RuntimeError("重建尚未结束，需使用相同目标显式resume")
                cursor = state.get("reindex_cursor", "")
            else:
                if state.get("phase") == "sync":
                    raise RuntimeError("请先中止正在执行的同步")
                if (state.get("embedding_fingerprint") not in (None, fingerprint)
                        and state.get("index_key") == index_key):
                    raise ValueError("Embedding变化必须使用新的空集合")
                cursor = ""
            result = await tx.run(
                """
                MATCH (s:KEOKnowledgeBase {name: $name})
                SET s.version = s.version + 1, s.status = 'updating', s.phase = 'reindex',
                    s.owner = $owner, s.target_fingerprint = $fingerprint,
                    s.target_index_key = $index_key, s.reindex_cursor = $cursor,
                    s.error_message = null
                RETURN s.version AS version
                """, name=self.STATE_NAME, owner=self._writer_id,
                fingerprint=fingerprint, index_key=index_key, cursor=cursor,
            )
            return (await result.single(strict=True))["version"]
        version = await self._execute_write(begin)

        while True:
            async def page(tx):
                state = await self._lock_state(tx)
                if (state.get("phase") != "reindex" or state.get("owner") != self._writer_id
                        or state["version"] != version):
                    raise RuntimeError("索引重建所有者或版本已变化")
                result = await tx.run(
                    """
                    MATCH (n:KEOEntity) WHERE n.node_id > $cursor
                    RETURN n.node_id AS id ORDER BY n.node_id LIMIT $limit
                    """, cursor=state.get("reindex_cursor", ""), limit=self.settings.VECTOR_SYNC_BATCH_SIZE,
                )
                ids = [row["id"] async for row in result]
                await self._queue_vector_tasks(tx, ids, "upsert", version)
                result = await tx.run(
                    """
                    MATCH (s:KEOKnowledgeBase {name: $name})
                    SET s.reindex_cursor = $cursor, s.phase = $phase
                    """, name=self.STATE_NAME,
                    cursor=ids[-1] if ids else state.get("reindex_cursor", ""),
                    phase="reindex" if ids else "sync",
                )
                await result.consume()
                return bool(ids)
            if not await self._execute_write(page):
                return version

    async def _query_state(self, tx, fingerprint, index_key, expected_version=None):
        result = await tx.run(
            "MATCH (s:KEOKnowledgeBase {name: $name}) RETURN properties(s) AS state",
            name=self.STATE_NAME,
        )
        state = (await result.single(strict=True))["state"]
        if (state["status"] != "ready" or state.get("phase", "idle") != "idle"
                or state.get("embedding_fingerprint") != fingerprint or state.get("index_key") != index_key):
            raise RuntimeError("图谱与向量索引未就绪或配置不一致")
        if expected_version is not None and state["version"] != expected_version:
            raise RuntimeError("查询期间知识库版本发生变化，请重试")
        return state["version"]

    async def assert_query_ready(self, fingerprint: str, index_key: str, expected_version=None) -> int:
        """查询Chroma前后均调用；返回版本供Neo4j查询及上下文重建核验。"""
        self._identifier(fingerprint, "fingerprint")
        self._identifier(index_key, "index_key")
        return await self._execute_read(self._query_state, fingerprint, index_key, expected_version)

    async def _evidence_index(self, tx, require_online=True):
        result = await tx.run(
            "SHOW FULLTEXT INDEXES YIELD name, state, labelsOrTypes, properties, options "
            "WHERE name = $name RETURN state, labelsOrTypes, properties, options",
            name=self.settings.EVIDENCE_FULLTEXT_INDEX,
        )
        rows = [dict(row) async for row in result]
        if not rows:
            raise RuntimeError("证据全文索引不存在，请先运行 graph_rag.evidence_index")
        row = rows[0]
        options = row["options"].get("indexConfig", {})
        if (row["labelsOrTypes"] != ["KEOEvidence"] or row["properties"] != ["text"]
                or options.get("fulltext.analyzer") != self.settings.EVIDENCE_FULLTEXT_ANALYZER
                or options.get("fulltext.eventually_consistent", False)):
            raise RuntimeError("证据全文索引定义不匹配，请使用新索引名称初始化")
        if require_online and row["state"] != "ONLINE":
            raise RuntimeError("证据全文索引尚未ONLINE")
        return row

    async def initialize_evidence_index(self) -> dict:
        """显式建立索引，不删除、覆盖已有索引，不改变节点和证据正文。"""
        name = self.settings.EVIDENCE_FULLTEXT_INDEX
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", name):
            raise ValueError("全文索引名称必须是字母开头的字母数字下划线")

        async def create(tx):
            result = await tx.run("CALL db.index.fulltext.listAvailableAnalyzers() YIELD analyzer RETURN analyzer")
            analyzers = [row["analyzer"] async for row in result]
            if self.settings.EVIDENCE_FULLTEXT_ANALYZER not in analyzers:
                raise ValueError("配置的中文分析器不可用；可用分析器：" + ", ".join(analyzers))
            result = await tx.run(
                f"CREATE FULLTEXT INDEX `{name}` IF NOT EXISTS FOR (e:KEOEvidence) ON EACH [e.text] "
                "OPTIONS $options", options={"indexConfig": {
                    "fulltext.analyzer": self.settings.EVIDENCE_FULLTEXT_ANALYZER,
                    "fulltext.eventually_consistent": False,
                }},
            )
            await result.consume()
        await self._execute_write(create)

        # 用短事务轮询，避免长时间等待超过日常查询事务的超时配置。
        deadline = asyncio.get_running_loop().time() + 600
        while True:
            row = await self._execute_read(lambda tx: self._evidence_index(tx, require_online=False))
            if row["state"] == "ONLINE":
                return row
            if row["state"] == "FAILED":
                raise RuntimeError("全文索引建立失败，请检查 SHOW FULLTEXT INDEXES")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("全文索引仍在建立；可稍后重新运行初始化命令")
            await asyncio.sleep(1)

    async def search_evidence(self, query: str, fingerprint: str, index_key: str,
                              expected_version: int) -> List[EvidenceSearchHit]:
        """全文索引有限召回；原文、来源及关联事实均来自建库侧。"""
        text_query = fulltext_query(query, self.settings.EVIDENCE_QUERY_MAX_TERMS)
        build_ids = sorted(self._load_build_ids())

        async def read(tx):
            await self._query_state(tx, fingerprint, index_key, expected_version)
            await self._evidence_index(tx)
            if not text_query:
                return []
            result = await tx.run(
                """
                CALL db.index.fulltext.queryNodes($name, $text_query, {limit: $pool}) YIELD node, score
                WHERE node.source_id IN $build_ids AND size(node.text) <= $chars
                WITH node, score ORDER BY score DESC, node.source_id, node.unit_id LIMIT $top_k
                CALL {
                    WITH node
                    MATCH (f:KEOFact)-[:SUPPORTED_BY]->(node)
                    WHERE f.source_id = node.source_id
                    WITH DISTINCT f ORDER BY f.triple_id LIMIT $per_hit
                    RETURN collect(f.triple_id) AS triple_ids
                }
                RETURN node.source_id AS source_id, node.unit_id AS unit_id,
                       node.text AS text, score, triple_ids
                ORDER BY score DESC, source_id, unit_id
                """, name=self.settings.EVIDENCE_FULLTEXT_INDEX, text_query=text_query,
                pool=self.settings.EVIDENCE_SEARCH_POOL,
                top_k=min(self.settings.EVIDENCE_SEARCH_TOP_K,self.settings.EVIDENCE_SEARCH_POOL),
                chars=self.settings.EVIDENCE_HIT_MAX_CHARS, build_ids=build_ids,
                per_hit=self.settings.EVIDENCE_FACTS_PER_HIT,
            )
            hits = [EvidenceSearchHit.model_validate(dict(row)) async for row in result]
            await self._query_state(tx, fingerprint, index_key, expected_version)
            return hits
        return await self._execute_read(read)

    async def get_case_facts(self, cases: Dict[str, List[str]], fingerprint: str,
                             index_key: str, expected_version: int, expand=False,
                             candidates=False) -> GraphSubgraph:
        """读取命中事实，或补齐同案例内与命中事实共享端点的有限事实。"""
        if not cases:
            return GraphSubgraph()
        case_limit = self.settings.EVIDENCE_SEARCH_TOP_K if candidates else self.settings.RETRIEVAL_MAX_CASES
        if len(cases) > case_limit:
            raise ValueError("入选案例数量超过限制")
        allowed = self._load_build_ids()
        records = []
        for source_id, ids in cases.items():
            if source_id not in allowed:
                raise ValueError("拒绝读取非build来源")
            ids = list(dict.fromkeys(self._identifier(v, "triple_id") for v in ids))
            if not ids or len(ids) > self.settings.MAX_EDGES:
                raise ValueError("命中事实数量无效")
            records.append({"source_id":source_id,"triple_ids":ids,"rank":len(records)})

        async def read(tx):
            await self._query_state(tx, fingerprint, index_key, expected_version)
            result = await tx.run(
                """
                UNWIND $cases AS item
                CALL {
                    WITH item
                    UNWIND item.triple_ids AS id
                    MATCH (a:KEOFact {triple_id:id})
                    WHERE a.source_id = item.source_id
                    MATCH (a)-[:HEAD|TAIL]->(n:KEOEntity)<-[:HEAD|TAIL]-(f:KEOFact)
                    WHERE f.source_id = item.source_id AND ($expand OR f.triple_id IN item.triple_ids)
                    WITH DISTINCT f, item
                    ORDER BY CASE WHEN f.triple_id IN item.triple_ids THEN 0 ELSE 1 END, f.triple_id
                    LIMIT $per_case
                    MATCH (f)-[:HEAD]->(h:KEOEntity)
                    MATCH (f)-[:TAIL]->(t:KEOEntity)
                    RETURN properties(f) AS fact, properties(h) AS head, properties(t) AS tail
                }
                RETURN fact, head, tail ORDER BY item.rank, fact.triple_id LIMIT $limit
                """, cases=records, expand=expand,
                per_case=self.settings.CASE_FACT_CANDIDATES, limit=self.settings.MAX_EDGES,
            )
            nodes, edges = {}, {}
            async for row in result:
                head, tail = (NodeMapping.model_validate(row[k]) for k in ("head","tail"))
                if len(nodes.keys() | {head.node_id,tail.node_id}) > self.settings.MAX_NODES:
                    continue
                edge = GraphEdge.model_validate({**row["fact"],
                    "head_node_id":head.node_id,"tail_node_id":tail.node_id})
                if edge.source_id not in cases:
                    raise RuntimeError("返回事实超出入选案例")
                nodes[head.node_id], nodes[tail.node_id] = head, tail
                edges[edge.triple_id] = edge
            await self._query_state(tx, fingerprint, index_key, expected_version)
            return GraphSubgraph(nodes=list(nodes.values()),edges=list(edges.values()))
        return await self._execute_read(read)

    async def get_subgraph(
        self, seed_ids: List[str], fingerprint: str, index_key: str,
        expected_version: int, hops: Optional[int] = None, query: str = "",
    ) -> GraphSubgraph:
        """Rank bounded per-frontier pools before admitting nodes/edges.

        Pools are deterministic samples, not an exhaustive high-degree search.
        No unbounded paths, evidence texts or whole-graph loads are used here.
        """
        self._limit(len(seed_ids), self.settings.SEED_TOP_K, "seeds")
        seeds = list(dict.fromkeys(self._identifier(v, "node_id") for v in seed_ids))
        hops = self.settings.MAX_HOPS if hops is None else hops
        self._limit(hops, self.settings.MAX_HOPS, "hops")
        build_ids = sorted(self._load_build_ids())
        pool_limit = self.settings.MAX_NEIGHBORS_PER_NODE * self.settings.NEIGHBOR_CANDIDATE_FACTOR

        async def read(tx):
            await self._query_state(tx, fingerprint, index_key, expected_version)
            nodes, edges = {}, {}
            result = await tx.run(
                "UNWIND $ids AS id MATCH (n:KEOEntity {node_id:id}) "
                "RETURN properties(n) AS node ORDER BY n.node_id LIMIT $limit",
                ids=seeds, limit=self.settings.MAX_NODES,
            )
            async for row in result:
                node = NodeMapping.model_validate(row["node"])
                nodes[node.node_id] = node
            frontier, expanded = [v for v in seeds if v in nodes], set()
            case_counts = Counter()
            for _ in range(hops):
                candidates = {}
                for node_id in frontier:
                    if node_id in expanded:
                        continue
                    expanded.add(node_id)
                    result = await tx.run(
                        """
                        MATCH (n:KEOEntity {node_id:$id})<-[:HEAD|TAIL]-(f:KEOFact)
                        WHERE f.source_id IN $build_ids
                        WITH DISTINCT f ORDER BY f.triple_id LIMIT $pool
                        MATCH (f)-[:HEAD]->(h:KEOEntity)
                        MATCH (f)-[:TAIL]->(t:KEOEntity)
                        RETURN properties(f) AS fact, properties(h) AS head, properties(t) AS tail
                        ORDER BY f.triple_id LIMIT $pool
                        """, id=node_id, build_ids=build_ids, pool=pool_limit,
                    )
                    local = []
                    async for row in result:
                        head = NodeMapping.model_validate(row["head"])
                        tail = NodeMapping.model_validate(row["tail"])
                        edge = GraphEdge.model_validate({**row["fact"],
                            "head_node_id":head.node_id, "tail_node_id":tail.node_id})
                        local.append((edge, head, tail))
                    texts = [f"{h.name} {e.relation} {t.name}" for e,h,t in local]
                    scored = list(zip(lexical_scores(query, texts), local))
                    scored.sort(key=lambda item:(-item[0],item[1][0].triple_id))
                    for score, item in scored[:self.settings.MAX_NEIGHBORS_PER_NODE]:
                        edge, head, tail = item
                        if edge.triple_id not in edges:
                            candidates[edge.triple_id] = (score, edge, head, tail)
                pending = list(candidates.values())
                next_frontier = []
                while pending and len(edges) < self.settings.MAX_EDGES:
                    # A soft diversity penalty prevents one case monopolizing the graph.
                    best = min(range(len(pending)), key=lambda i:(
                        -(pending[i][0] / (1 + 0.25 * case_counts[pending[i][1].source_id])),
                        pending[i][1].triple_id))
                    _, edge, head, tail = pending.pop(best)
                    new_ids = {head.node_id,tail.node_id} - nodes.keys()
                    if len(nodes) + len(new_ids) > self.settings.MAX_NODES:
                        continue
                    for node in (head,tail):
                        if node.node_id not in nodes:
                            next_frontier.append(node.node_id)
                        nodes[node.node_id] = node
                    edges[edge.triple_id] = edge
                    case_counts[edge.source_id] += 1
                frontier = list(dict.fromkeys(next_frontier))
                if not frontier or len(edges) >= self.settings.MAX_EDGES:
                    break
            await self._query_state(tx, fingerprint, index_key, expected_version)
            return GraphSubgraph(nodes=list(nodes.values()), edges=list(edges.values()))
        return await self._execute_read(read)

    async def get_evidence_for_facts(
        self, triple_ids: List[str], fingerprint: str, index_key: str,
        expected_version: int, query: str = "",
    ) -> Dict[str, List[EvidenceRecord]]:
        """Fetch bounded evidence candidates; final text budget belongs to ContextBuilder.

        Text is returned verbatim. Per-fact and total limits bound memory before
        relation selection, so long/irrelevant units cannot consume the final budget early.
        """
        if not triple_ids:
            return {}
        self._limit(len(triple_ids), self.settings.MAX_EDGES, "facts")
        ids = list(dict.fromkeys(self._identifier(v, "triple_id") for v in triple_ids))
        build_ids = sorted(self._load_build_ids())
        terms = query_terms(query)

        async def read(tx):
            await self._query_state(tx, fingerprint, index_key, expected_version)
            result = await tx.run(
                """
                UNWIND $ids AS id MATCH (f:KEOFact {triple_id:id})
                WHERE f.source_id IN $build_ids
                CALL {
                    WITH f
                    MATCH (f)-[:SUPPORTED_BY]->(e:KEOEvidence)
                    WHERE e.source_id = f.source_id AND size(e.text) <= $chars
                    WITH e, size([term IN $terms WHERE toLower(e.text) CONTAINS term]) AS relevance
                    ORDER BY relevance DESC, e.unit_id
                    LIMIT $per_fact
                    RETURN e, relevance
                }
                WITH e, max(relevance) AS relevance, collect(DISTINCT f.triple_id) AS triple_ids
                RETURN e.source_id AS source_id, e.unit_id AS unit_id,
                       e.text AS text, triple_ids
                ORDER BY relevance DESC, source_id, unit_id LIMIT $limit
                """, ids=ids, build_ids=build_ids, terms=terms,
                chars=min(self.settings.MAX_CONTEXT_CHARS,self.settings.CONTEXT_TARGET_CHARS),
                per_fact=self.settings.EVIDENCE_PER_FACT,
                limit=self.settings.MAX_EVIDENCE_CANDIDATES,
            )
            output = {}
            async for row in result:
                evidence = EvidenceRecord.model_validate(dict(row))
                for tid in row["triple_ids"]:
                    output.setdefault(tid, []).append(evidence)
            await self._query_state(tx, fingerprint, index_key, expected_version)
            return output
        return await self._execute_read(read)


async def main() -> None:
    """手动初始化实验库，不导入业务数据。"""

    async with Neo4jStore() as store:
        await store.initialize_schema()

        state = await store.get_state()

        print("Neo4j连接及结构初始化成功")
        print(state.model_dump())


if __name__ == "__main__":
    asyncio.run(main())
