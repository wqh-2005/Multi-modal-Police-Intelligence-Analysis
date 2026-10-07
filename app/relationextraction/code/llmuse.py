from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import configuration_audit, get_llm_settings, path_cfg
from .llmclient import ExtractionResult, LLMClient


FACTUALITY_NAMES = {
    "ASSERTED": "明确发生",
    "REQUESTED": "被要求执行",
    "QUESTIONED": "疑问表达",
    "NEGATED": "否定表达",
    "HYPOTHETICAL": "假设表达",
    "UNCERTAIN": "不确定",
}
SOURCE_NAMES = {"dialogue": "对话", "case": "案例"}
MAX_CONCURRENCY = 8
EXTRACTION_PIPELINE_VERSION = "2.0-chinese-label-normalization"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_dialogue(source_id: str, text: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"([A-Za-z])\s*[：:]", text))
    units = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[match.end() : end].strip().strip('"“”')
        if content:
            units.append(
                {
                    "unit_id": f"{source_id}_turn_{len(units) + 1:03d}",
                    "speaker": match.group(1).upper(),
                    "text": content,
                }
            )
    if not units:
        units.append({"unit_id": f"{source_id}_turn_001", "speaker": None, "text": text})
    return units


def split_case(source_id: str, text: str) -> list[dict[str, Any]]:
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", text) if part.strip()]
    if not parts:
        parts = [text]
    return [
        {"unit_id": f"{source_id}_clause_{index:03d}", "speaker": None, "text": part}
        for index, part in enumerate(parts, start=1)
    ]


def iter_dialogues(path: Path) -> Iterator[dict[str, Any]]:
    data = read_json(path)
    items = data.get("items", data)
    for index, item in enumerate(items, start=1):
        source_id = str(item.get("id") or f"dialogue_{index:06d}")
        text = str(item.get("dialogue") or "").strip()
        if text:
            yield {
                "source_id": source_id,
                "source_type": "对话",
                "fraud_category": item.get("fraud_category_standard", ""),
                "source_units": split_dialogue(source_id, text),
            }


def _ijson_items(path: Path) -> Iterable[dict[str, Any]]:
    try:
        import ijson
    except ImportError as exc:
        raise RuntimeError(
            "缺少流式JSON依赖ijson，请执行：python -m pip install -r requirements.txt"
        ) from exc
    with path.open("rb") as file:
        yield from ijson.items(file, "item")


def iter_cases(path: Path) -> Iterator[dict[str, Any]]:
    for index, item in enumerate(_ijson_items(path), start=1):
        source_id = str(item.get("id") or item.get("source_id") or f"case_{index:06d}")
        text = str(item.get("案情描述") or item.get("text") or "").strip()
        if text:
            yield {
                "source_id": source_id,
                "source_type": "案例",
                "fraud_category": item.get("案件类别", ""),
                "source_units": split_case(source_id, text),
            }


def iter_documents(dialogue_file: Path, case_file: Path, source_type: str) -> Iterator[dict[str, Any]]:
    if source_type in {"all", "dialogue"}:
        yield from iter_dialogues(dialogue_file)
    if source_type in {"all", "case"}:
        yield from iter_cases(case_file)


def selected_documents(
    dialogue_file: Path,
    case_file: Path,
    source_type: str,
    start_index: int,
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    documents = iter_documents(dialogue_file, case_file, source_type)
    stop = None if limit is None else start_index + limit
    yield from itertools.islice(documents, start_index, stop)


def count_documents(dialogue_file: Path, case_file: Path) -> dict[str, int]:
    dialogue = sum(1 for _ in iter_dialogues(dialogue_file))
    case = sum(1 for _ in iter_cases(case_file))
    return {"dialogue": dialogue, "case": case, "all": dialogue + case}


def load_schemas() -> tuple[dict[str, Any], dict[str, Any], str, str]:
    entity_data = read_json(path_cfg.ENTITY_SCHEMA)
    relation_data = read_json(path_cfg.RELATION_SCHEMA)
    entity_lines = [
        f"{item['标准ID']}（{item['名称']}）：{item['定义']}"
        for item in entity_data["实体类型"]
    ]
    role_lines = [
        f"{item['标准ID']}（{item['名称']}）：{item['定义']}"
        for item in entity_data["人员角色"]
    ]
    entity_text = "实体类型：\n" + "\n".join(entity_lines) + "\n人物角色：\n" + "\n".join(role_lines)
    relation_lines = []
    entity_name_to_id = {
        item["名称"]: item["标准ID"] for item in entity_data["实体类型"]
    }
    for item in relation_data["关系类型"]:
        heads = "/".join(
            f"{entity_name_to_id.get(name, name)}（{name}）"
            for name in item["允许的头实体类型"]
        )
        tails = "/".join(
            f"{entity_name_to_id.get(name, name)}（{name}）"
            for name in item["允许的尾实体类型"]
        )
        relation_lines.append(
            f"{item['标准关系ID']}（{item['关系名称']}），{heads} -> {tails}：{item['定义']}"
        )
    factuality_lines = [
        f"{item['标准ID']}（{item['中文名称']}）" for item in relation_data["事实状态"]
    ]
    relation_text = "关系：\n" + "\n".join(relation_lines) + "\n事实状态：\n" + "\n".join(factuality_lines)
    return entity_data, relation_data, entity_text, relation_text


def build_indexes(
    entity_data: dict[str, Any], relation_data: dict[str, Any]
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, Any]], dict[str, str]]:
    entity_names = {item["标准ID"]: item["名称"] for item in entity_data["实体类型"]}
    role_names = {item["标准ID"]: item["名称"] for item in entity_data["人员角色"]}
    name_to_entity = {name: entity_id for entity_id, name in entity_names.items()}
    relations = {item["标准关系ID"]: item for item in relation_data["关系类型"]}
    return entity_names, role_names, relations, name_to_entity


def convert_result(
    document: dict[str, Any],
    result: ExtractionResult,
    entity_names: dict[str, str],
    role_names: dict[str, str],
    relations: dict[str, dict[str, Any]],
    name_to_entity: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    units = {unit["unit_id"]: unit["text"] for unit in document["source_units"]}
    entity_ids: dict[tuple[str, str, str | None], str] = {}
    triples: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    role_name_to_id = {name: role_id for role_id, name in role_names.items()}
    relation_name_to_id = {
        item["关系名称"]: relation_id for relation_id, item in relations.items()
    }
    factuality_name_to_id = {name: fact_id for fact_id, name in FACTUALITY_NAMES.items()}

    def normalize_entity_type(value: str) -> str:
        cleaned = value.strip()
        upper = cleaned.upper()
        if upper in entity_names:
            return upper
        return name_to_entity.get(cleaned, cleaned)

    def normalize_role(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        upper = cleaned.upper()
        if upper in role_names:
            return upper
        return role_name_to_id.get(cleaned, cleaned)

    def normalize_relation(value: str) -> str:
        cleaned = value.strip()
        upper = cleaned.upper()
        if upper in relations:
            return upper
        return relation_name_to_id.get(cleaned, cleaned)

    def normalize_factuality(value: str) -> str:
        cleaned = value.strip()
        upper = cleaned.upper()
        if upper in FACTUALITY_NAMES:
            return upper
        return factuality_name_to_id.get(cleaned, "UNCERTAIN")

    def entity_id(name: str, entity_type: str, role: str | None) -> str:
        key = (name, entity_type, role)
        if key not in entity_ids:
            entity_ids[key] = f"{document['source_id']}_E{len(entity_ids) + 1:03d}"
        return entity_ids[key]

    for draft in result.triples:
        relation_id = normalize_relation(draft.relation)
        head_type = normalize_entity_type(draft.head.type)
        tail_type = normalize_entity_type(draft.tail.type)
        relation = relations.get(relation_id)
        if relation is None:
            warnings.append(f"忽略未知关系：{draft.relation}")
            continue
        if head_type not in entity_names or tail_type not in entity_names:
            warnings.append(f"忽略未知实体类型：{draft.head.type}->{draft.tail.type}")
            continue
        allowed_heads = {
            name_to_entity[name] for name in relation["允许的头实体类型"] if name in name_to_entity
        }
        allowed_tails = {
            name_to_entity[name] for name in relation["允许的尾实体类型"] if name in name_to_entity
        }
        if head_type not in allowed_heads or tail_type not in allowed_tails:
            warnings.append(
                f"忽略类型不匹配关系：{relation_id}（{head_type}->{tail_type}；"
                f"允许{'/'.join(sorted(allowed_heads))}->{'/'.join(sorted(allowed_tails))}）"
            )
            continue
        evidence_ids = list(dict.fromkeys(draft.evidence_unit_ids))
        if not evidence_ids or any(unit_id not in units for unit_id in evidence_ids):
            warnings.append(f"忽略证据编号无效关系：{relation_id}")
            continue
        head_role = normalize_role(draft.head.role) if head_type == "PERSON" else None
        tail_role = normalize_role(draft.tail.role) if tail_type == "PERSON" else None
        if head_role not in role_names:
            head_role = "UNKNOWN" if draft.head.type == "PERSON" else None
        if tail_role not in role_names:
            tail_role = "UNKNOWN" if draft.tail.type == "PERSON" else None
        key = (
            draft.head.name,
            head_type,
            relation_id,
            draft.tail.name,
            tail_type,
            tuple(evidence_ids),
        )
        if key in seen:
            continue
        seen.add(key)
        triples.append(
            {
                "triple_id": f"{document['source_id']}_T{len(triples) + 1:03d}",
                "source_id": document["source_id"],
                "source_type": document["source_type"],
                "fraud_category": document["fraud_category"],
                "head": {
                    "id": entity_id(draft.head.name, head_type, head_role),
                    "name": draft.head.name,
                    "type": entity_names[head_type],
                    "role": role_names.get(head_role) if head_role else None,
                },
                "relation": relation["关系名称"],
                "tail": {
                    "id": entity_id(draft.tail.name, tail_type, tail_role),
                    "name": draft.tail.name,
                    "type": entity_names[tail_type],
                    "role": role_names.get(tail_role) if tail_role else None,
                },
                "factuality": FACTUALITY_NAMES[normalize_factuality(draft.factuality)],
                "evidence": [{"unit_id": unit_id, "text": units[unit_id]} for unit_id in evidence_ids],
                "confidence": round(draft.confidence, 4),
                "status": "待人工审核",
            }
        )
    return triples, warnings


def safe_source_id(source_id: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]", "_", source_id)
    return safe if safe == source_id else f"{safe}_{hashlib.sha1(source_id.encode()).hexdigest()[:8]}"


def source_group(document: dict[str, Any]) -> str:
    return "dialogue" if document["source_type"] == "对话" else "case"


def source_output_dir(output_dir: Path, group: str) -> Path:
    return output_dir / group


def document_path(output_dir: Path, category: str, document: dict[str, Any]) -> Path:
    group = source_group(document)
    shard = hashlib.sha1(document["source_id"].encode("utf-8")).hexdigest()[:2]
    return source_output_dir(output_dir, group) / category / shard / f"{safe_source_id(document['source_id'])}.json"


def cached_success(
    path: Path, source_id: str, extraction_fingerprint: str | None = None
) -> bool:
    if not path.exists():
        return False
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    valid = (
        payload.get("status") == "success"
        and payload.get("source_id") == source_id
        and isinstance(payload.get("triples"), list)
    )
    if extraction_fingerprint is not None:
        valid = valid and payload.get("extraction_fingerprint") == extraction_fingerprint
    return valid


def selection_total(counts: dict[str, int], source_type: str, start: int, limit: int | None) -> int:
    available = max(counts[source_type] - start, 0)
    return available if limit is None else min(available, limit)


def selected_source_groups(
    counts: dict[str, int], source_type: str, start: int, limit: int | None
) -> list[str]:
    if source_type != "all":
        return [source_type]
    end = counts["all"] if limit is None else min(start + limit, counts["all"])
    groups: list[str] = []
    if start < counts["dialogue"] and end > 0:
        groups.append("dialogue")
    if end > counts["dialogue"] and start < counts["all"]:
        groups.append("case")
    return groups


def stable_manifest(
    dialogue_file: Path, case_file: Path, model: str, max_tokens: int
) -> dict[str, Any]:
    stable = {
        "pipeline_version": EXTRACTION_PIPELINE_VERSION,
        "model": model,
        "runtime": {"max_tokens": max_tokens, "temperature": 0},
        "inputs": {
            "dialogue": {"path": str(dialogue_file), "sha256": file_sha256(dialogue_file)},
            "case": {"path": str(case_file), "sha256": file_sha256(case_file)},
        },
        "resources": {
            "entity_schema_sha256": file_sha256(path_cfg.ENTITY_SCHEMA),
            "relation_schema_sha256": file_sha256(path_cfg.RELATION_SCHEMA),
            "prompt_sha256": file_sha256(path_cfg.PROMPT_FILE),
        },
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    stable["fingerprint"] = hashlib.sha256(encoded).hexdigest()
    return stable


def document_fingerprint(
    document: dict[str, Any],
    model: str,
    max_tokens: int,
    resources: dict[str, str],
) -> tuple[str, str]:
    document_json = json.dumps(document, ensure_ascii=False, sort_keys=True)
    document_sha256 = hashlib.sha256(document_json.encode("utf-8")).hexdigest()
    extraction = {
        "pipeline_version": EXTRACTION_PIPELINE_VERSION,
        "document_sha256": document_sha256,
        "model": model,
        "runtime": {"max_tokens": max_tokens, "temperature": 0},
        "resources": resources,
    }
    encoded = json.dumps(extraction, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return document_sha256, hashlib.sha256(encoded).hexdigest()


def prepare_manifest(
    output_dir: Path,
    stable: dict[str, Any],
    filters: dict[str, Any],
) -> None:
    path = output_dir / "run_manifest.json"
    existing = read_json(path) if path.exists() else None
    history = list(existing.get("history", [])) if existing else []
    if existing and existing.get("fingerprint") != stable["fingerprint"]:
        previous = {
            "fingerprint": existing.get("fingerprint"),
            "model": existing.get("model"),
            "runtime": existing.get("runtime"),
            "inputs": existing.get("inputs"),
            "resources": existing.get("resources"),
            "last_finished_at": existing.get("last_finished_at"),
            "superseded_at": now_iso(),
        }
        if not history or history[-1].get("fingerprint") != previous["fingerprint"]:
            history.append(previous)
    payload = {
        **stable,
        "created_at": existing.get("created_at") if existing else now_iso(),
        "last_started_at": now_iso(),
        "last_filters": filters,
        "history": history,
    }
    atomic_write_json(path, payload)


def finish_manifest(output_dir: Path, counters: dict[str, int], selected: int) -> None:
    path = output_dir / "run_manifest.json"
    payload = read_json(path)
    payload["last_finished_at"] = now_iso()
    payload["last_result"] = {"selected": selected, **counters}
    atomic_write_json(path, payload)


async def process_document(
    document: dict[str, Any],
    output_dir: Path,
    client: LLMClient,
    schema_bundle: tuple[
        str,
        str,
        dict[str, str],
        dict[str, str],
        dict[str, dict[str, Any]],
        dict[str, str],
    ],
    max_retries: int,
    max_tokens: int,
    resources: dict[str, str],
    overwrite: bool,
) -> str:
    result_file = document_path(output_dir, "by_document", document)
    failure_file = document_path(output_dir, "failures", document)
    document_sha256, extraction_fingerprint = document_fingerprint(
        document, client.model_name, max_tokens, resources
    )
    if not overwrite and cached_success(
        result_file, document["source_id"], extraction_fingerprint
    ):
        return "skipped"
    entity_text, relation_text, entity_names, role_names, relations, name_to_entity = schema_bundle
    last_error: Exception | None = None
    started = time.perf_counter()
    for attempt in range(1, max_retries + 1):
        try:
            call = await client.extract(
                json.dumps(document, ensure_ascii=False), entity_text, relation_text
            )
            if call.result.source_id != document["source_id"]:
                raise ValueError(f"模型返回source_id错误：{call.result.source_id}")
            triples, warnings = convert_result(
                document, call.result, entity_names, role_names, relations, name_to_entity
            )
            atomic_write_json(
                result_file,
                {
                    "status": "success",
                    "source_id": document["source_id"],
                    "source_type": document["source_type"],
                    "model": client.model_name,
                    "document_sha256": document_sha256,
                    "extraction_fingerprint": extraction_fingerprint,
                    "completed_at": now_iso(),
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "attempts": attempt,
                    "usage": call.usage,
                    "triples": triples,
                    "warnings": warnings,
                },
            )
            if failure_file.exists():
                failure_file.unlink()
            return "completed"
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                await asyncio.sleep(min(2 ** (attempt - 1), 10))
    assert last_error is not None
    atomic_write_json(
        failure_file,
        {
            "status": "failed",
            "source_id": document["source_id"],
            "source_type": document["source_type"],
            "failed_at": now_iso(),
            "attempts": max_retries,
            "error_type": type(last_error).__name__,
            "error": str(last_error)[:1000],
        },
    )
    return "failed"


async def run_batch(args: argparse.Namespace) -> None:
    dialogue_file, case_file, output_dir = Path(args.dialogue), Path(args.case), Path(args.output)
    counts = count_documents(dialogue_file, case_file)
    total = selection_total(counts, args.source_type, args.start_index, args.limit)
    settings = get_llm_settings()
    entity_data, relation_data, entity_text, relation_text = load_schemas()
    entity_names, role_names, relations, name_to_entity = build_indexes(entity_data, relation_data)
    schema_bundle = (
        entity_text,
        relation_text,
        entity_names,
        role_names,
        relations,
        name_to_entity,
    )
    system_prompt = path_cfg.PROMPT_FILE.read_text(encoding="utf-8")
    client = LLMClient(system_prompt, settings)
    filters = {
        "source_type": args.source_type,
        "start_index": args.start_index,
        "limit": args.limit,
        "concurrency": args.concurrency,
    }
    groups = selected_source_groups(counts, args.source_type, args.start_index, args.limit)
    manifest = stable_manifest(
        dialogue_file,
        case_file,
        settings.EXTRACTION_MODEL,
        settings.LLM_MAX_TOKENS,
    )
    for group in groups:
        prepare_manifest(source_output_dir(output_dir, group), manifest, filters)
    queue: asyncio.Queue[tuple[int, dict[str, Any]] | None] = asyncio.Queue(
        maxsize=max(args.concurrency * 2, 2)
    )
    counters = {"completed": 0, "skipped": 0, "failed": 0}
    source_counters = {
        group: {"completed": 0, "skipped": 0, "failed": 0} for group in groups
    }
    source_selected = {group: 0 for group in groups}

    async def producer() -> None:
        docs = selected_documents(
            dialogue_file, case_file, args.source_type, args.start_index, args.limit
        )
        for index, document in enumerate(docs, start=1):
            await queue.put((index, document))
        for _ in range(args.concurrency):
            await queue.put(None)

    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                index, document = item
                outcome = await process_document(
                    document,
                    output_dir,
                    client,
                    schema_bundle,
                    settings.LLM_MAX_RETRIES,
                    settings.LLM_MAX_TOKENS,
                    manifest["resources"],
                    args.overwrite,
                )
                counters[outcome] += 1
                group = source_group(document)
                source_counters[group][outcome] += 1
                source_selected[group] += 1
                print(f"[{index}/{total}] {document['source_id']}: {outcome}")
            finally:
                queue.task_done()

    producer_task = asyncio.create_task(producer())
    workers = [asyncio.create_task(worker()) for _ in range(args.concurrency)]
    await asyncio.gather(producer_task, *workers)
    for group in groups:
        finish_manifest(
            source_output_dir(output_dir, group),
            source_counters[group],
            source_selected[group],
        )
    print(json.dumps({"selected": total, **counters}, ensure_ascii=False, indent=2))
    print("正在自动生成triples.jsonl和summary.json……")
    export_results(args)


def iter_selected_from_args(args: argparse.Namespace) -> Iterator[dict[str, Any]]:
    return selected_documents(
        Path(args.dialogue),
        Path(args.case),
        args.source_type,
        args.start_index,
        args.limit,
    )


def collect_status(args: argparse.Namespace) -> dict[str, Any]:
    success = failed = pending = triples = 0
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for document in iter_selected_from_args(args):
        result_file = document_path(Path(args.output), "by_document", document)
        failure_file = document_path(Path(args.output), "failures", document)
        if cached_success(result_file, document["source_id"]):
            payload = read_json(result_file)
            success += 1
            triples += len(payload["triples"])
            for key in token_usage:
                token_usage[key] += int(payload.get("usage", {}).get(key, 0) or 0)
        elif failure_file.exists():
            failed += 1
        else:
            pending += 1
    return {
        "success": success,
        "failed": failed,
        "pending": pending,
        "documents": success + failed + pending,
        "triples": triples,
        "usage": token_usage,
    }


def export_results(args: argparse.Namespace) -> None:
    output_dir = Path(args.output)
    counts = count_documents(Path(args.dialogue), Path(args.case))
    groups = selected_source_groups(counts, args.source_type, args.start_index, args.limit)
    summaries = {
        group: {"success": 0, "failed": 0, "pending": 0, "documents": 0, "triples": 0}
        for group in groups
    }
    files: dict[str, Any] = {}
    temporaries: dict[str, Path] = {}
    try:
        for group in groups:
            group_dir = source_output_dir(output_dir, group)
            group_dir.mkdir(parents=True, exist_ok=True)
            temporary = group_dir / "triples.jsonl.tmp"
            temporaries[group] = temporary
            files[group] = temporary.open("w", encoding="utf-8", newline="\n")
        for document in iter_selected_from_args(args):
            group = source_group(document)
            summary = summaries[group]
            file = files[group]
            summary["documents"] += 1
            result_file = document_path(output_dir, "by_document", document)
            failure_file = document_path(output_dir, "failures", document)
            if cached_success(result_file, document["source_id"]):
                payload = read_json(result_file)
                summary["success"] += 1
                for triple in payload["triples"]:
                    file.write(json.dumps(triple, ensure_ascii=False) + "\n")
                    summary["triples"] += 1
            elif failure_file.exists():
                summary["failed"] += 1
            else:
                summary["pending"] += 1
    finally:
        for file in files.values():
            file.close()
    exported_at = now_iso()
    for group in groups:
        group_dir = source_output_dir(output_dir, group)
        temporaries[group].replace(group_dir / "triples.jsonl")
        summaries[group]["exported_at"] = exported_at
        atomic_write_json(group_dir / "summary.json", summaries[group])
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


def check_environment(args: argparse.Namespace) -> None:
    dialogue_file, case_file = Path(args.dialogue), Path(args.case)
    paths = {
        "dialogue": dialogue_file,
        "case": case_file,
        "entity_schema": path_cfg.ENTITY_SCHEMA,
        "relation_schema": path_cfg.RELATION_SCHEMA,
        "prompt": path_cfg.PROMPT_FILE,
    }
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"缺少文件：{', '.join(missing)}")
    load_schemas()
    counts = count_documents(dialogue_file, case_file)
    audit = configuration_audit()
    payload = {
        "status": "ok" if all(audit["required_keys"].values()) else "configuration_missing",
        "paths": {name: str(path) for name, path in paths.items()},
        "configuration": audit,
        "documents": counts,
        "api_called": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dialogue", default=str(path_cfg.DIALOGUE_FILE))
    parser.add_argument("--case", default=str(path_cfg.CASE_FILE))
    parser.add_argument("--source-type", choices=["all", "dialogue", "case"], default="all")
    parser.add_argument("--start-index", type=int, default=0, help="筛选来源后的0基起始位置")
    parser.add_argument("--limit", type=int, default=None)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="诈骗关系三元组LLM全量批处理")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="检查配置和数据，不调用API")
    add_selection_arguments(check_parser)

    run_parser = subparsers.add_parser("run", help="执行LLM抽取")
    add_selection_arguments(run_parser)
    run_parser.add_argument("--output", default=str(path_cfg.OUTPUT_DIR))
    run_parser.add_argument("--concurrency", type=int, default=1)
    run_parser.add_argument("--overwrite", action="store_true")

    for command, help_text in (("status", "查看运行状态"), ("export", "汇总成功结果")):
        child = subparsers.add_parser(command, help=help_text)
        add_selection_arguments(child)
        child.add_argument("--output", default=str(path_cfg.OUTPUT_DIR))
    args = parser.parse_args()
    if args.start_index < 0:
        parser.error("--start-index不能小于0")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit必须大于0")
    if args.command == "run" and not 1 <= args.concurrency <= MAX_CONCURRENCY:
        parser.error(f"--concurrency必须在1到{MAX_CONCURRENCY}之间")
    return args


def main() -> None:
    args = parse_args()
    if args.command == "check":
        check_environment(args)
    elif args.command == "run":
        asyncio.run(run_batch(args))
    elif args.command == "status":
        print(json.dumps(collect_status(args), ensure_ascii=False, indent=2))
    elif args.command == "export":
        export_results(args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
