"""把 A / D 的交接包导入项目数据目录，并登记校验信息。

为什么要有这一步：``temp/`` 只是他人的暂存交接目录，随时可能被清空或覆盖，
运行期直接读它等于把实验依赖挂在一个不可控的位置上。本模块负责一次性把
B 需要的文件拷进 ``experiments/KEO/data/<版本>/``，记录 SHA256 与条数，
之后所有代码只读项目内的数据。

用法（仓库根执行）：

    python -m experiments.KEO.core.ingest
    python -m experiments.KEO.core.ingest --dry-run
    python -m experiments.KEO.core.ingest --force          # 已导入也重拷
    python -m experiments.KEO.core.ingest --include-full   # 额外导入 1.1 GB 完整包

校验分三层：
1. 有源 manifest 的文件，比对 SHA256（A 的 b 包提供）；
2. 没有源 manifest 的文件，导入后记录本项目的 SHA256，供后续比对；
3. 跨文件条数一致性：build_ids.txt、划分名单.json、cases_build.jsonl 三者应一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from experiments.KEO.configs import config

# (源相对路径, 目标相对路径, A manifest 中用于校验的键)
ITEMS: list[tuple[str, str, str | None]] = [
    ("person_a_v1.1_b_建库原文/b/cases_build.jsonl", "build/cases_build.jsonl", "cases_build.jsonl"),
    ("person_a_v1.1_b_建库原文/b/build_ids.txt", "build/build_ids.txt", "build_ids.txt"),
    ("person_a_v1.1_b_建库原文/b/dev_ids.txt", "build/dev_ids.txt", "dev_ids.txt"),
    ("person_a_v1.1_keo_完整交付包/keo/划分名单.json", "splits/划分名单.json", None),
    ("D正式题库/Demo版/questions.jsonl", "questions/Demo版/questions.jsonl", None),
    ("D正式题库/全量版/questions_full.jsonl", "questions/全量版/questions_full.jsonl", None),
]

FULL_ITEMS: list[tuple[str, str, str | None]] = [
    ("person_a_v1.1_keo_完整交付包/keo/案例数据.json", "full/案例数据.json", None),
    ("person_a_v1.1_keo_完整交付包/keo/三元组数据.jsonl", "full/三元组数据.jsonl", None),
    ("person_a_v1.1_keo_完整交付包/keo/实体别名映射.json", "full/实体别名映射.json", None),
]

A_MANIFEST = "person_a_v1.1_b_建库原文/b/manifest.json"


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def load_a_hashes(source_root: Path) -> dict[str, str]:
    """读取 A 的 b 包 manifest，取出文件名到 SHA256 的映射。"""
    manifest_path = source_root / A_MANIFEST
    if not manifest_path.exists():
        return {}
    with manifest_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return {
        name: info.get("sha256", "")
        for name, info in (data.get("files") or {}).items()
    }


def count_lines(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def import_one(source: Path, target: Path, expected: str | None,
               force: bool, dry_run: bool) -> dict:
    if not source.exists():
        raise FileNotFoundError(f"交接包缺少文件：{source}")

    if target.exists() and not force:
        actual = sha256_of(target)
        if expected and actual != expected:
            raise ValueError(
                f"已导入文件与源 manifest 不一致：{target}\n"
                f"  期望 {expected}\n  实际 {actual}\n"
                "确认源已更新后加 --force 重新导入。"
            )
        return {"path": str(target.relative_to(config.DATA_DIR)),
                "size_bytes": target.stat().st_size, "sha256": actual,
                "verified": bool(expected), "action": "skip"}

    if dry_run:
        return {"path": str(target.relative_to(config.DATA_DIR)),
                "size_bytes": source.stat().st_size, "sha256": "",
                "verified": bool(expected), "action": "dry-run"}

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    shutil.copy2(source, tmp)
    tmp.replace(target)

    actual = sha256_of(target)
    if expected and actual != expected:
        target.unlink(missing_ok=True)
        raise ValueError(
            f"拷贝后校验失败：{target}\n  期望 {expected}\n  实际 {actual}"
        )
    return {"path": str(target.relative_to(config.DATA_DIR)),
            "size_bytes": target.stat().st_size, "sha256": actual,
            "verified": bool(expected), "action": "copied"}


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 A / D 交接包到项目数据目录")
    parser.add_argument("--version", default=config.DATASET_VERSION, help="数据集版本目录名")
    parser.add_argument("--source", type=Path, default=config.STAGING_ROOT,
                        help="交接包根目录，默认仓库根 temp/")
    parser.add_argument("--include-full", action="store_true",
                        help="额外导入 A 的完整包（案例主表、全量三元组、别名映射，约 1.2 GB）")
    parser.add_argument("--force", action="store_true", help="已导入也重新拷贝")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    args = parser.parse_args()

    source_root = Path(args.source)
    if not source_root.exists():
        raise FileNotFoundError(f"交接包根目录不存在：{source_root}")

    # 允许切换版本目录：--version 覆盖 config 的默认值
    data_dir = config.DATA_DIR
    if args.version != config.DATASET_VERSION:
        data_dir = config.KEO_ROOT / "data" / args.version

    print(f"交接包根目录 : {source_root}")
    print(f"目标数据目录 : {data_dir}")
    print(f"版本         : {args.version}")

    a_hashes = load_a_hashes(source_root)
    items = ITEMS + (FULL_ITEMS if args.include_full else [])
    records: dict[str, dict] = {}
    failed = 0

    for relative_source, relative_target, hash_key in items:
        expected = a_hashes.get(hash_key) if hash_key else None
        try:
            info = import_one(source_root / relative_source, data_dir / relative_target,
                              expected, args.force, args.dry_run)
        except Exception as exc:  # noqa: BLE001 - 逐条报告，不中断其余文件
            failed += 1
            print(f"  [失败] {relative_target}: {exc}")
            continue
        tag = {"skip": "跳过", "copied": "已导入", "dry-run": "待导入"}[info["action"]]
        mark = "已校验" if info["verified"] else "已记录"
        print(f"  [{tag}] {info['path']:<34} {info['size_bytes'] / 1024 / 1024:>8.1f} MB  {mark}")
        records[info["path"]] = {k: info[k] for k in ("size_bytes", "sha256", "verified")}

    if failed:
        print(f"\n有 {failed} 个文件未完成，manifest 不写入")
        return 1
    if args.dry_run:
        print("\n dry-run：未写入任何文件")
        return 0

    # 条数一致性检查
    counts: dict[str, int] = {}
    try:
        counts["build_ids_txt"] = count_lines(data_dir / "build" / "build_ids.txt")
        counts["dev_ids_txt"] = count_lines(data_dir / "build" / "dev_ids.txt")
        counts["cases_build"] = count_lines(data_dir / "build" / "cases_build.jsonl")
        with (data_dir / "splits" / "划分名单.json").open(encoding="utf-8") as handle:
            splits = json.load(handle)
        counts["splits_build"] = len(splits.get("build_ids", []))
        counts["splits_eval"] = len(splits.get("eval_ids", []))
        counts["questions_demo"] = count_lines(data_dir / "questions" / "Demo版" / "questions.jsonl")
        counts["questions_full"] = count_lines(data_dir / "questions" / "全量版" / "questions_full.jsonl")
    except Exception as exc:  # noqa: BLE001
        print(f"条数统计失败：{exc}")
        return 1

    same = counts["build_ids_txt"] == counts["splits_build"] == counts["cases_build"]
    print("\n条数：")
    for name, value in counts.items():
        print(f"  {name:<16} {value:>8,}")
    print(f"  build_ids / splits.build / cases_build 三者一致：{same}")

    manifest = {
        "dataset_version": args.version,
        "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "files": records,
        "counts": counts,
        "consistent": same,
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = data_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已写入登记：{manifest_path}")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
