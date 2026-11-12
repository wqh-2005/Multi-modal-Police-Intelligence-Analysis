"""图谱RAG流式导入器。

不持有完整NetworkX图。
不依赖graph.json。
不自动修改或删除已有事实。
"""

import argparse
import asyncio
import json
import logging
import os
import tempfile

from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path
from time import perf_counter
from typing import Dict, List, Optional

from experiments.KEO.core.graph_rag.neo4j_store import Neo4jStore
from experiments.KEO.core.graph_rag.node_mapper import NodeMapper
from experiments.KEO.models.graph_schema import (
    NodeMapping,
    TripleInput,
)


logger = getLogger(__name__)


class GraphBuilder:
    """按建库名单流式导入三元组。

    store的连接由调用方管理。

    同一时间只运行一个导入器，且导入期间不运行向量同步。
    """

    def __init__(
        self,
        store: Neo4jStore,
        mapper: Optional[NodeMapper] = None,
    ):
        self.store = store
        self.mapper = mapper or NodeMapper()
        self.settings = store.settings

    @staticmethod
    def _load_split(
        path: Path,
    ) -> tuple[set[str], set[str]]:
        """校验划分名单，不修改或重新划分。"""

        with path.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("划分名单必须是JSON对象")

        groups = {}

        for field in ("build_ids", "eval_ids"):
            values = data.get(field)

            if not isinstance(values, list):
                raise ValueError(f"{field}必须是列表")

            for value in values:
                if not isinstance(value, str):
                    raise ValueError(f"{field}中的ID必须是字符串")

                if not value or value != value.strip():
                    raise ValueError(
                        f"{field}中的ID不能为空或包含首尾空白"
                    )

            ids = set(values)

            if len(ids) != len(values):
                raise ValueError(f"{field}存在重复ID")

            groups[field] = ids

        build_ids = groups["build_ids"]
        eval_ids = groups["eval_ids"]

        if not build_ids:
            raise ValueError("build_ids不能为空")

        if build_ids & eval_ids:
            raise ValueError("build_ids与eval_ids存在交叉")

        return build_ids, eval_ids

    @staticmethod
    def _check_identifier(value: str, field: str) -> None:
        """检查ID，不改写上游编号。"""

        if not value or value != value.strip():
            raise ValueError(
                f"{field}不能为空或包含首尾空白"
            )

    def _validate_triple(self, triple: TripleInput) -> None:
        """补充输入模型以外的结构一致性检查。"""

        self._check_identifier(triple.triple_id, "triple_id")
        self._check_identifier(triple.source_id, "source_id")

        for field in (
            "source_type",
            "fraud_category",
            "relation",
            "factuality",
        ):
            if not getattr(triple, field).strip():
                raise ValueError(f"{field}不能仅包含空白")

        # 同一条记录中，相同实体ID必须对应相同原实体。
        entities = {}

        for entity in (triple.head, triple.tail):
            self._check_identifier(entity.id, "entity.id")

            if not entity.type.strip():
                raise ValueError("实体type不能为空白")

            content = entity.model_dump()

            if (
                entity.id in entities
                and entities[entity.id] != content
            ):
                raise ValueError(
                    f"同一三元组内原实体内容冲突：{entity.id}"
                )

            entities[entity.id] = content

        evidence_texts = {}

        for evidence in triple.evidence:
            self._check_identifier(evidence.unit_id, "unit_id")

            if not evidence.text.strip():
                raise ValueError("证据text不能仅包含空白")

            previous = evidence_texts.get(evidence.unit_id)

            if previous is not None and previous != evidence.text:
                raise ValueError(
                    f"同一证据单元文本冲突：{evidence.unit_id}"
                )

            evidence_texts[evidence.unit_id] = evidence.text

    @staticmethod
    def _fact_signature(triple: TripleInput) -> dict:
        """用于检查同批重复事实。

        证据按unit_id比较，顺序变化不视为事实变化。
        不将A的额外附加字段当作Neo4j事实属性。
        """

        return {
            "source_id": triple.source_id,
            "source_type": triple.source_type,
            "fraud_category": triple.fraud_category,
            "head": triple.head.model_dump(),
            "tail": triple.tail.model_dump(),
            "relation": triple.relation,
            "factuality": triple.factuality,
            "confidence": triple.confidence,
            "status": triple.status,
            "evidence": {
                item.unit_id: item.text
                for item in triple.evidence
            },
        }

    def _prepare_mappings(
        self,
        triple: TripleInput,
        mappings: Dict[str, NodeMapping],
        original_entities: dict,
        node_properties: dict,
        evidence_texts: dict,
    ) -> None:
        """检查本批原实体、共享节点和证据的一致性。"""

        for entity in (triple.head, triple.tail):
            original = {
                "source_id": triple.source_id,
                **entity.model_dump(),
            }

            previous = original_entities.get(entity.id)

            if previous is not None and previous != original:
                raise ValueError(
                    f"同批原实体内容冲突：{entity.id}"
                )

            mapping = self.mapper.map_entity(
                entity.model_dump(),
                triple.source_id,
            )

            previous_node = node_properties.get(mapping.node_id)

            if (
                previous_node is not None
                and previous_node != mapping
            ):
                raise ValueError(
                    f"同批图谱节点属性冲突：{mapping.node_id}"
                )

            original_entities[entity.id] = original
            node_properties[mapping.node_id] = mapping
            mappings[entity.id] = mapping

        for evidence in triple.evidence:
            key = (triple.source_id, evidence.unit_id)
            previous_text = evidence_texts.get(key)

            if (
                previous_text is not None
                and previous_text != evidence.text
            ):
                raise ValueError(
                    f"同批证据文本冲突：{key}"
                )

            evidence_texts[key] = evidence.text

    @staticmethod
    def _save_report(path: Path, report: dict) -> None:
        """先写临时文件，再原子替换报告，避免半份JSON。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".import_report_",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary_path = Path(file.name)

                json.dump(
                    report,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
                file.write("\n")

            os.replace(temporary_path, path)

        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def build_from_jsonl(
        self,
        triples_path: Optional[str | Path] = None,
        split_path: Optional[str | Path] = None,
        report_path: Optional[str | Path] = None,
        dry_run: bool = False,
    ) -> dict:
        """流式检查或导入三元组。

        dry_run：
            检查名单、输入记录及当前批次一致性，
            不连接数据库、不创建约束、不写入数据。

            不检查数据库中的旧数据冲突，
            不保证跨批次的原实体/证据一致性。
            正式导入时由Neo4jStore检查这些冲突。

        正式导入：
            每批独立提交。失败不会撤销此前成功批次。
            相同数据可重跑，不自动覆盖内容变化的记录。
        """

        triples_path = Path(
            triples_path
            if triples_path is not None
            else self.settings.TRIPLES_PATH
        ).expanduser().resolve()

        split_path = Path(
            split_path
            if split_path is not None
            else self.settings.SPLIT_PATH
        ).expanduser().resolve()

        if report_path is None:
            timestamp = datetime.now(timezone.utc).strftime(
                "%Y%m%dT%H%M%S%fZ"
            )
            report_path = (
                triples_path.parent
                / f"import_report_{timestamp}.json"
            )
        else:
            report_path = Path(report_path).expanduser().resolve()

        # 防止报告覆盖输入文件。
        if report_path in (triples_path, split_path):
            raise ValueError("报告路径不能与输入文件相同")

        started = perf_counter()

        report = {
            "status": "running",
            "dry_run": dry_run,
            "triples_path": str(triples_path),
            "split_path": str(split_path),
            "report_path": str(report_path),
            "line_number": 0,
            "nonempty_lines": 0,
            "build_rows": 0,
            "skipped_eval_rows": 0,
            "skipped_outside_split_rows": 0,
            "duplicate_rows_within_batches": 0,
            "processed_batches": 0,
            "committed_batches": 0,
            # 各批接收的不同triple_id数量之和，不是新增数。
            "accepted_batch_items": 0,
            "last_committed_line": 0,
            "current_batch_first_line": None,
            "current_batch_last_line": None,
            "build_case_count": 0,
            "build_ids_without_triples": [],
            "elapsed_ms": 0,
            "error": None,
        }

        triples: List[TripleInput] = []
        mappings: Dict[str, NodeMapping] = {}

        original_entities = {}
        node_properties = {}
        evidence_texts = {}
        fact_signatures = {}

        seen_build_ids = set()
        build_ids = set()
        maintenance_started = False

        # 文件由外部工具同时改写不受支持。
        initial_stat = None

        def clear_batch():
            triples.clear()
            mappings.clear()
            original_entities.clear()
            node_properties.clear()
            evidence_texts.clear()
            fact_signatures.clear()

            report["current_batch_first_line"] = None
            report["current_batch_last_line"] = None

        async def flush():
            if not triples:
                return

            if dry_run:
                accepted = len(triples)
            else:
                accepted = await self.store.write_batch(
                    triples,
                    mappings,
                )
                report["committed_batches"] += 1
                report["last_committed_line"] = report[
                    "current_batch_last_line"
                ]

            report["processed_batches"] += 1
            report["accepted_batch_items"] += accepted

            logger.info(
                "处理批次=%d，提交批次=%d，建库记录=%d，扫描至第%d行",
                report["processed_batches"],
                report["committed_batches"],
                report["build_rows"],
                report["line_number"],
            )

            clear_batch()

        try:
            if not triples_path.is_file():
                raise FileNotFoundError(
                    f"三元组文件不存在：{triples_path}"
                )

            if not split_path.is_file():
                raise FileNotFoundError(
                    f"划分名单不存在：{split_path}"
                )

            build_ids, eval_ids = self._load_split(split_path)

            # 提前验证报告目录可写，避免导入完才发现无法保存报告。
            self._save_report(report_path, report)

            if not dry_run:
                await self.store.initialize_schema()
                await self.store.begin_import()
                maintenance_started = True

            initial_stat = triples_path.stat()
            batch_size = self.settings.IMPORT_BATCH_SIZE

            with triples_path.open(
                "r", encoding="utf-8-sig"
            ) as file:
                for line_number, line in enumerate(file, 1):
                    report["line_number"] = line_number

                    if not line.strip():
                        continue

                    report["nonempty_lines"] += 1

                    record = json.loads(line)

                    if not isinstance(record, dict):
                        raise ValueError(
                            "JSONL每行必须是一个JSON对象"
                        )

                    source_id = record.get("source_id")

                    if not isinstance(source_id, str):
                        raise ValueError(
                            "source_id必须是字符串"
                        )

                    self._check_identifier(
                        source_id,
                        "source_id",
                    )

                    if source_id not in build_ids:
                        if source_id in eval_ids:
                            report["skipped_eval_rows"] += 1
                        else:
                            report[
                                "skipped_outside_split_rows"
                            ] += 1

                        if report["nonempty_lines"] % 1000 == 0:
                            await asyncio.sleep(0)

                        continue

                    if not triples:
                        report["current_batch_first_line"] = (
                            line_number
                        )

                    report["current_batch_last_line"] = (
                        line_number
                    )

                    triple = TripleInput.model_validate(record)
                    self._validate_triple(triple)

                    signature = self._fact_signature(triple)
                    previous = fact_signatures.get(
                        triple.triple_id
                    )

                    if previous is not None:
                        if previous != signature:
                            raise ValueError(
                                "同批重复triple_id内容冲突："
                                f"{triple.triple_id}"
                            )

                        report[
                            "duplicate_rows_within_batches"
                        ] += 1
                    else:
                        self._prepare_mappings(
                            triple,
                            mappings,
                            original_entities,
                            node_properties,
                            evidence_texts,
                        )

                        fact_signatures[
                            triple.triple_id
                        ] = signature

                        triples.append(triple)

                    report["build_rows"] += 1
                    seen_build_ids.add(source_id)

                    if len(triples) >= batch_size:
                        await flush()

            # 检测常见的读取期间文件变化。
            final_stat = triples_path.stat()

            if (
                final_stat.st_size != initial_stat.st_size
                or final_stat.st_mtime_ns
                != initial_stat.st_mtime_ns
            ):
                raise RuntimeError(
                    "三元组文件在导入期间发生变化，"
                    "请固定输入文件后重新执行"
                )

            if report["build_rows"] == 0:
                raise ValueError(
                    "没有找到属于build_ids的有效三元组"
                )

            await flush()

            if not dry_run:
                await self.store.finish_import()
                state = await self.store.get_state()

                if state.status != "updating":
                    raise RuntimeError(
                        "导入期间知识库状态被其他流程修改，"
                        "请检查是否同时运行了同步或其他写入任务"
                    )

            report["status"] = "validated" if dry_run else "imported"

        except BaseException as exc:
            report["status"] = (
                "cancelled"
                if isinstance(
                    exc,
                    (asyncio.CancelledError, KeyboardInterrupt),
                )
                else "error"
            )

            # 不把原始案情、数据库参数或服务返回内容写入报告。
            report["error"] = {
                "type": type(exc).__name__,
                "message": (
                    f"处理至第{report['line_number']}行时失败；"
                    "当前批次未确认提交，检查终端异常原因。"
                ),
            }

            if maintenance_started:
                try:
                    await self.store.mark_import_error(
                        report["error"]["message"]
                    )
                except Exception:
                    # 若连接已中断，仍不能将状态标记为ready。
                    logger.error(
                        "无法写入失败状态；请检查Neo4j连接。"
                    )

            raise

        finally:
            report["build_case_count"] = len(seen_build_ids)
            report["build_ids_without_triples"] = sorted(
                build_ids - seen_build_ids
            )

            report["elapsed_ms"] = round(
                (perf_counter() - started) * 1000,
                2,
            )

            # 报告保存失败不覆盖原来的导入异常。
            try:
                self._save_report(report_path, report)
            except Exception:
                if report["status"] in ("imported", "validated"):
                    raise

                logger.error(
                    "导入失败报告未能保存：%s",
                    report_path,
                )

        return report


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="按build_ids流式导入Neo4j实验库"
    )
    parser.add_argument(
        "--triples",
        type=Path,
        help="三元组JSONL路径，默认使用配置",
    )
    parser.add_argument(
        "--split",
        type=Path,
        help="划分名单JSON路径，默认使用配置",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="报告路径，默认在三元组目录生成独立报告",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查文件，不连接或写入Neo4j",
    )
    args = parser.parse_args()

    store = Neo4jStore()
    builder = GraphBuilder(store)

    try:
        if not args.dry_run:
            await store.connect()

        report = await builder.build_from_jsonl(
            triples_path=args.triples,
            split_path=args.split,
            report_path=args.report,
            dry_run=args.dry_run,
        )

        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            )
        )

        if args.dry_run:
            print("文件检查完成，未写入Neo4j。")
        else:
            print(
                "Neo4j导入完成，知识库仍为updating；"
                "完成节点向量同步和核验后才能开放检索。"
            )

    finally:
        await store.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(main())
