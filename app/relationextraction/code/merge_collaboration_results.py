from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import path_cfg


RESOURCE_FILES = {
    "entity_schema_sha256": path_cfg.ENTITY_SCHEMA,
    "relation_schema_sha256": path_cfg.RELATION_SCHEMA,
    "prompt_sha256": path_cfg.PROMPT_FILE,
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"JSON顶层必须是对象：{path}")
    return value


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def accepted_resource_hashes(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8-sig")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return {
        sha256_bytes(normalized.encode("utf-8")),
        sha256_bytes(normalized.replace("\n", "\r\n").encode("utf-8")),
    }


def parse_case_number(source_id: str) -> int:
    prefix = "case_"
    if not source_id.startswith(prefix) or not source_id[len(prefix) :].isdigit():
        raise ValueError(f"非法案例编号：{source_id}")
    return int(source_id[len(prefix) :])


def collect_partition(
    name: str,
    case_dir: Path,
    first_case: int,
    last_case: int,
) -> dict[str, Any]:
    manifest_path = case_dir / "run_manifest.json"
    by_document_dir = case_dir / "by_document"
    failures_dir = case_dir / "failures"
    if not manifest_path.is_file() or not by_document_dir.is_dir():
        raise FileNotFoundError(f"{name}目录不完整：{case_dir}")

    manifest = read_json(manifest_path)
    successes: dict[str, Path] = {}
    triple_count = 0
    for path in by_document_dir.rglob("*.json"):
        payload = read_json(path)
        source_id = str(payload.get("source_id", ""))
        number = parse_case_number(source_id)
        if not first_case <= number <= last_case:
            raise ValueError(f"{name}出现区间外结果：{source_id}")
        if payload.get("status") != "success" or payload.get("source_type") != "案例":
            raise ValueError(f"{name}结果状态或来源类型错误：{path}")
        triples = payload.get("triples")
        if not isinstance(triples, list):
            raise ValueError(f"{name}的triples不是数组：{path}")
        if source_id in successes:
            raise ValueError(f"{name}出现重复成功结果：{source_id}")
        for triple in triples:
            if not isinstance(triple, dict) or triple.get("source_id") != source_id:
                raise ValueError(f"{name}三元组source_id不一致：{path}")
        successes[source_id] = path
        triple_count += len(triples)

    failures: dict[str, Path] = {}
    if failures_dir.is_dir():
        for path in failures_dir.rglob("*.json"):
            payload = read_json(path)
            source_id = str(payload.get("source_id", ""))
            number = parse_case_number(source_id)
            if not first_case <= number <= last_case:
                raise ValueError(f"{name}出现区间外失败记录：{source_id}")
            if payload.get("status") != "failed":
                raise ValueError(f"{name}失败记录状态错误：{path}")
            if source_id in failures:
                raise ValueError(f"{name}出现重复失败记录：{source_id}")
            failures[source_id] = path

    overlap = set(successes) & set(failures)
    if overlap:
        raise ValueError(f"{name}同时存在成功与失败记录：{sorted(overlap)[:5]}")
    expected = {f"case_{number:06d}" for number in range(first_case, last_case + 1)}
    actual = set(successes) | set(failures)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{name}区间不完整，缺失={missing[:10]}，多余={extra[:10]}")

    return {
        "name": name,
        "case_dir": case_dir,
        "range": [first_case, last_case],
        "manifest": manifest,
        "manifest_sha256": sha256_file(manifest_path),
        "successes": successes,
        "failures": failures,
        "triple_count": triple_count,
    }


def validate_configuration(partitions: list[dict[str, Any]]) -> dict[str, Any]:
    first = partitions[0]["manifest"]
    stable_fields = {
        "pipeline_version": first.get("pipeline_version"),
        "model": first.get("model"),
        "runtime": first.get("runtime"),
        "case_input_sha256": first.get("inputs", {}).get("case", {}).get("sha256"),
        "dialogue_input_sha256": first.get("inputs", {}).get("dialogue", {}).get("sha256"),
    }
    for partition in partitions[1:]:
        manifest = partition["manifest"]
        comparison = {
            "pipeline_version": manifest.get("pipeline_version"),
            "model": manifest.get("model"),
            "runtime": manifest.get("runtime"),
            "case_input_sha256": manifest.get("inputs", {}).get("case", {}).get("sha256"),
            "dialogue_input_sha256": manifest.get("inputs", {}).get("dialogue", {}).get("sha256"),
        }
        if comparison != stable_fields:
            raise ValueError(f"{partition['name']}运行配置与学生A不一致：{comparison}")

    canonical_resources: dict[str, str] = {}
    for field, resource_path in RESOURCE_FILES.items():
        accepted = accepted_resource_hashes(resource_path)
        canonical_resources[field] = sha256_bytes(
            resource_path.read_text(encoding="utf-8-sig")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .encode("utf-8")
        )
        for partition in partitions:
            actual = partition["manifest"].get("resources", {}).get(field)
            if actual not in accepted:
                raise ValueError(
                    f"{partition['name']}的{field}不是当前资源的LF或CRLF版本：{actual}"
                )
    return {**stable_fields, "resources_canonical_lf": canonical_resources}


def copy_without_conflict(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(source) != sha256_file(destination):
            raise FileExistsError(f"目标存在不同内容，拒绝覆盖：{destination}")
        return "identical"
    temporary = destination.with_suffix(destination.suffix + ".merge_tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)
    return "copied"


def merge(args: argparse.Namespace) -> None:
    target = Path(args.target).resolve()
    partitions = [
        collect_partition("student_a", target, 1, 27404),
        collect_partition("student_b", Path(args.student_b).resolve(), 27405, 54807),
        collect_partition("student_c", Path(args.student_c).resolve(), 54808, 82210),
    ]
    configuration = validate_configuration(partitions)

    all_successes: set[str] = set()
    all_failures: set[str] = set()
    for partition in partitions:
        overlap = all_successes & set(partition["successes"])
        overlap |= all_failures & set(partition["failures"])
        overlap |= all_successes & set(partition["failures"])
        overlap |= all_failures & set(partition["successes"])
        if overlap:
            raise ValueError(f"分区之间出现重复编号：{sorted(overlap)[:10]}")
        all_successes.update(partition["successes"])
        all_failures.update(partition["failures"])

    copied = identical = 0
    for partition in partitions[1:]:
        for category, records in (
            ("by_document", partition["successes"]),
            ("failures", partition["failures"]),
        ):
            source_root = partition["case_dir"] / category
            target_root = target / category
            for source in records.values():
                outcome = copy_without_conflict(source, target_root / source.relative_to(source_root))
                if outcome == "copied":
                    copied += 1
                else:
                    identical += 1

    report = {
        "merged_at": now_iso(),
        "target": str(target),
        "configuration": configuration,
        "line_ending_note": "资源文本内容一致；清单哈希差异仅由LF与CRLF换行造成。",
        "documents": 82210,
        "success": len(all_successes),
        "failed": len(all_failures),
        "pending": 82210 - len(all_successes) - len(all_failures),
        "triples_before_export": sum(item["triple_count"] for item in partitions),
        "failed_source_ids": sorted(all_failures),
        "copy_result": {"copied": copied, "identical_existing": identical},
        "partitions": [
            {
                "name": item["name"],
                "case_dir": str(item["case_dir"]),
                "range": item["range"],
                "success": len(item["successes"]),
                "failed": len(item["failures"]),
                "triples": item["triple_count"],
                "run_manifest_sha256": item["manifest_sha256"],
                "recorded_resources": item["manifest"].get("resources", {}),
            }
            for item in partitions
        ],
    }
    output = target / "collaboration_merge_manifest.json"
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="安全合并三人案例抽取的逐文档结果")
    parser.add_argument("--target", required=True, help="学生A现有case目录，也是合并目标")
    parser.add_argument("--student-b", required=True, help="学生B的case目录")
    parser.add_argument("--student-c", required=True, help="学生C的case目录")
    return parser


if __name__ == "__main__":
    merge(build_parser().parse_args())
