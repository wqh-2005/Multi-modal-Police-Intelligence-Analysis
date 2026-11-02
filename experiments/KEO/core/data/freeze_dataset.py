from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


from .paths import (
    CLEANING_DIR,
    HANDOFF_DIR_V11,
    HANDOFF_ROOT_V11,
    NAME_CLEANING_DIR,
    PERSON_A_DIR,
    REPO_ROOT,
    abs_path,
    rel,
)

DEFAULT_ROOT = REPO_ROOT
DEFAULT_PERSON_A = PERSON_A_DIR
DEFAULT_CLEANING = CLEANING_DIR
DEFAULT_HANDOFF = HANDOFF_DIR_V11

CLEANING_FILES = (
    "triples_clean_v2.jsonl",
    "实体别名映射.json",
    "name_cleaning_changes.csv.gz",
    "name_cleaning_summary.json",
    "name_cleaning_report.md",
)

AUDIT_FILES = (
    "cases_master.jsonl",
    "triples_clean.jsonl",
    "issues_triples.csv.gz",
    "issues_summary.csv",
    "split_manifest.csv",
    "split_summary.csv",
    "split_build_ids.txt",
    "split_eval_ids.txt",
    "excluded_cases.csv",
    "duplicate_text_groups.csv",
    "decisions.md",
    "audit_report.md",
)

HANDOFF_FILES = ("案例数据.json", "三元组数据.jsonl", "划分名单.json", "实体别名映射.json", "交付说明.md")

# B/C/D 角色分包（由 export_handoff.py 生成）
SUBPACKAGES = {
    "b": ("cases_build.jsonl", "build_ids.txt", "dev_ids.txt", "manifest.json"),
    "c": ("triples_build.jsonl", "build_ids.txt", "实体别名映射.json", "归一化规则.md", "manifest.json"),
    "d": ("cases_eval.jsonl", "eval_ids.txt", "manifest.json"),
}

SCRIPT_FILES = (
    "experiments/KEO/core/data/build_dataset.py",
    "experiments/KEO/core/data/audit_entity_names.py",
    "experiments/KEO/core/data/apply_name_cleaning.py",
    "experiments/KEO/core/data/export_keo.py",
    "experiments/KEO/core/data/export_handoff.py",
    "experiments/KEO/core/data/freeze_dataset.py",
    "experiments/KEO/core/data/paths.py",
    "app/relationextraction/code/merge_collaboration_results.py",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_entry(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


def collect(base: Path, names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for name in names:
        path = base / name
        if not path.is_file():
            missing.append(name)
            continue
        entries[name] = file_entry(path)
    if missing:
        raise SystemExit(f"缺少冻结必需文件（{base}）：{missing}")
    return entries


def build_freeze_note(manifest: dict[str, Any], cleaning: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 人员 A 数据集冻结说明")
    lines.append("")
    lines.append(f"> 版本：`{manifest['version']}`  ")
    lines.append(f"> 冻结时间：{manifest['frozen_at']}  ")
    if manifest.get("refrozen_at") and manifest["refrozen_at"] != manifest["frozen_at"]:
        lines.append(f"> 最近复冻（校验和重算）：{manifest['refrozen_at']}  ")
    lines.append(f"> 冻结方：{manifest['frozen_by']}  ")
    lines.append(f"> 状态：**{manifest['status']}**  ")
    lines.append("> 路径基准：仓库根目录 `backend/`（manifest 中所有路径均为相对路径）")
    lines.append("")
    lines.append("## 1. 冻结范围")
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("|---|---|")
    lines.append("| 案例 / 可用案例 | {:,} / {:,} |".format(manifest["counts"]["documents"], manifest["counts"]["usable_documents"]))
    lines.append("| 排除案例 | {:,} |".format(manifest["counts"]["excluded_documents"]))
    lines.append("| 三元组 | {:,} |".format(manifest["counts"]["triples"]))
    lines.append("| 建库 / 评估案例 | {:,} / {:,} |".format(manifest["counts"]["build"], manifest["counts"]["eval"]))
    lines.append("| 划分种子 / 比例 | {} / {} |".format(manifest["seed"], manifest["train_ratio"]))
    lines.append("| 名称清洗版本 | {} |".format(cleaning.get("version", "")))
    lines.append("")
    lines.append("## 2. 正式交付包（KEO 格式 1.1）")
    lines.append("")
    lines.append("目录：`{}`".format(rel(Path(manifest["handoff"]["dir"]))))
    lines.append("")
    lines.append("| 文件 | 大小 | SHA256 |")
    lines.append("|---|---:|---|")
    for name, entry in manifest["handoff"]["files"].items():
        lines.append(f"| `{name}` | {entry['size_bytes']:,} B | `{entry['sha256']}` |")
    lines.append("")
    packages = manifest.get("handoff", {}).get("packages") or {}
    if packages:
        lines.append("### 2.1 角色分包（B / C / D）")
        lines.append("")
        lines.append("| 包 | 角色 | 文件 | SHA256 |")
        lines.append("|---|---|---|---|")
        for role, payload in packages.items():
            for name, entry in payload["files"].items():
                lines.append(f"| `{role}/` | {payload.get('role', role.upper())} | `{name}` | `{entry['sha256']}` |")
        lines.append("")
        lines.append("三方共用同一份 `划分名单.json`，`b/build_ids.txt` 与 `c/build_ids.txt` 完全一致；`d` 只含评估侧原文。")
        lines.append("")

    lines.append("## 3. 名称清洗结果")
    lines.append("")
    rules = cleaning.get("by_rule", {})
    lines.append("| 规则 | 影响提及 |")
    lines.append("|---|---:|")
    lines.append(f"| A 去首尾空白 | {rules.get('A_whitespace', 0):,} |")
    lines.append(f"| B2 空名称 → 未知 | {rules.get('B_empty_to_unknown', 0):,} |")
    lines.append(f"| C1 UNKNOWN → 未知 | {rules.get('C_unknown_to_unknown_cn', 0):,} |")
    lines.append(f"| D 自动别名合并 | {rules.get('D_auto_alias', 0):,} |")
    lines.append(f"| E review 官方化合并 | {rules.get('E_review_alias', 0):,} |")
    lines.append("")
    verification = cleaning.get("verification", {})
    lines.append("| 校验项 | 结果 |")
    lines.append("|---|---|")
    lines.append("| 三元组总数 | {:,} |".format(verification.get("triples", 0)))
    lines.append("| `triple_id` 唯一数 | {:,} |".format(verification.get("unique_triple_ids", 0)))
    lines.append("| 剩余空名称 / UNKNOWN / 首尾空白 | {} / {} / {} |".format(
        verification.get("empty_names_left", "-"),
        verification.get("unknown_left", "-"),
        verification.get("whitespace_left", "-"),
    ))
    lines.append("| 可继续合并的 auto 别名残留 | {} |".format(verification.get("alias_left", "-")))
    lines.append("")
    review = (manifest.get("name_cleaning") or {}).get("review") or {}
    if review:
        lines.append(
            "review 别名：{groups:,} 组中官方核验后合并 {merged:,} 组（{src}），保留 {kept} 组纯噪声；"
            "逐组证据见 `{decisions}`。".format(
                groups=review.get("groups", 0),
                merged=review.get("merged_groups", 0),
                src=review.get("official_source", ""),
                kept=review.get("kept_groups", 0),
                decisions=review.get("decisions", ""),
            )
        )
        lines.append("")
    lines.append("未合并项（按确认口径保留）：`hold` 898 组；人员不跨案合并；不同类型不合并。")
    lines.append("")
    lines.append("## 4. 输入与产物校验和")
    lines.append("")
    lines.append("| 角色 | 路径 | 大小 | SHA256 |")
    lines.append("|---|---|---:|---|")
    for section, label in (("inputs", "输入"), ("outputs", "审计产物"), ("cleaned", "清洗产物")):
        for name, entry in manifest[section].items():
            lines.append(f"| {label} | `{entry['path']}` | {entry['size_bytes']:,} B | `{entry['sha256']}` |")
    lines.append("")
    lines.append("## 5. 脚本版本")
    lines.append("")
    lines.append("| 脚本 | SHA256 |")
    lines.append("|---|---|")
    for name, entry in manifest["scripts"].items():
        lines.append(f"| `{name}` | `{entry['sha256']}` |")
    lines.append("")
    lines.append("运行环境：Python {} / {} / {}".format(
        manifest["environment"]["python"], manifest["environment"]["platform"], manifest["environment"]["implementation"]
    ))
    lines.append("")
    lines.append("## 6. 复现命令")
    lines.append("")
    lines.append("```powershell")
    lines.append("# 0) 重建划分（如需要，会重写 cases_master / split_*）")
    lines.append("python -m experiments.KEO.core.data.build_dataset")
    lines.append("")
    lines.append("# 1) 名称审计（只读）")
    lines.append("python -m experiments.KEO.core.data.audit_entity_names")
    lines.append("")
    lines.append("# 2) 执行名称清洗（P0-5）")
    lines.append("python -m experiments.KEO.core.data.apply_name_cleaning")
    lines.append("")
    lines.append("# 3) 导出 KEO 1.1 交付包")
    lines.append("python -m experiments.KEO.core.data.export_keo \\")
    lines.append("  --triples experiments/KEO/data/person_a/name_cleaning/apply_v1.0/triples_clean_v2.jsonl \\")
    lines.append("  --version 1.0-final --status frozen --overwrite")
    lines.append("")
    lines.append("# 4) 导出 B/C/D 交接包")
    lines.append("python -m experiments.KEO.core.data.export_handoff")
    lines.append("")
    lines.append("# 5) 冻结 manifest")
    lines.append("python -m experiments.KEO.core.data.freeze_dataset --version 1.0-final")
    lines.append("```")
    lines.append("")
    lines.append("## 7. 冻结规则")
    lines.append("")
    lines.append("- 冻结后不覆盖本版本目录；任何影响案例 ID、划分名单、三元组内容的修改都必须升级版本；")
    lines.append("- 只改文档或说明时升级次版本；")
    lines.append("- 后续变更请写入 `decisions.md` 并记录确认日期、确认方和影响范围。")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="冻结人员 A 数据集并写入 manifest/冻结说明")
    parser.add_argument("--person-a", default=str(DEFAULT_PERSON_A), help="person_a 目录")
    parser.add_argument("--cleaning-dir", default=str(DEFAULT_CLEANING), help="清洗产物目录（默认 apply_v1.1）")
    parser.add_argument("--handoff", default=str(DEFAULT_HANDOFF), help="共用交付包目录")
    parser.add_argument("--version", default="1.0-final", help="冻结版本号")
    parser.add_argument("--frozen-by", default="项目组", help="冻结方")
    parser.add_argument("--note", default="", help="冻结备注")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    person_a = Path(args.person_a).expanduser()
    manifest_path = person_a / "dataset_manifest.json"
    cleaning_summary_path = Path(args.cleaning_dir).expanduser() / "name_cleaning_summary.json"
    for path in (manifest_path, cleaning_summary_path):
        if not path.is_file():
            raise SystemExit(f"缺少冻结所需文件: {path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cleaning = json.loads(cleaning_summary_path.read_text(encoding="utf-8"))
    verification = cleaning.get("verification", {})
    if verification.get("triples") != manifest["counts"]["triples"]:
        raise SystemExit("清洗后三元组数量与 manifest 不一致，拒绝冻结")
    if verification.get("empty_names_left") or verification.get("unknown_left") or verification.get("alias_left"):
        raise SystemExit(f"清洗校验未通过，拒绝冻结: {verification}")

    frozen_at = datetime.now().astimezone().isoformat(timespec="seconds")
    backup = person_a / "dataset_manifest.draft.json"
    if not backup.exists():
        shutil.copyfile(manifest_path, backup)
    previous_version = manifest.get("version")
    archived_manifests: list[str] = []
    if previous_version and previous_version != args.version:
        archive = person_a / f"dataset_manifest_{previous_version}.json"
        if not archive.exists():
            shutil.copyfile(manifest_path, archive)
        archived_manifests.append(rel(archive))

    already_frozen = manifest.get("status") == "frozen"
    if not already_frozen:
        manifest.setdefault("history", []).append(
            {
                "status": manifest.get("status"),
                "version": manifest.get("version", "1.0-draft"),
                "created_at": manifest.get("created_at"),
                "replaced_at": frozen_at,
                "reason": f"升级到 {args.version}（名称清洗/review 官方化合并）",
            }
        )
        manifest["frozen_at"] = frozen_at
        manifest["refrozen_at"] = None
    else:
        # 首次冻结时间保持不变，后续重算只更新 refrozen_at
        manifest.setdefault("frozen_at", frozen_at)
        manifest["refrozen_at"] = frozen_at
    manifest["status"] = "frozen"
    manifest["supersedes"] = previous_version if previous_version and previous_version != args.version else manifest.get("supersedes")
    if archived_manifests:
        manifest["archived_manifests"] = sorted(set(manifest.get("archived_manifests", []) + archived_manifests))
    manifest["version"] = args.version
    manifest["frozen_by"] = args.frozen_by
    if args.note:
        manifest["freeze_note"] = args.note
    manifest["base_dir"] = rel(person_a)
    manifest["documents_note"] = (
        "decisions.md、audit_report.md、冻结说明.md 为活文档，会随版本更新；"
        "数据交付物哈希以 handoff / cleaned 两组为准。"
    )
    manifest["inputs"] = {
        "cases": file_entry(abs_path(manifest["inputs"]["cases"])),
        "triples": file_entry(abs_path(manifest["inputs"]["triples"])),
        "merge_manifest": file_entry(abs_path(manifest["inputs"]["merge_manifest"])),
    }
    manifest["outputs"] = collect(person_a, AUDIT_FILES)
    cleaning_dir = Path(args.cleaning_dir).expanduser() if args.cleaning_dir else DEFAULT_CLEANING
    manifest["cleaned"] = collect(cleaning_dir, CLEANING_FILES)
    manifest["handoff"] = {
        "dir": rel(Path(args.handoff).expanduser()),
        "format": "KEO 1.1",
        "version": args.version,
        "files": collect(Path(args.handoff).expanduser(), HANDOFF_FILES),
    }
    packages: dict[str, Any] = {}
    for role, names in SUBPACKAGES.items():
        role_dir = Path(args.handoff).expanduser().parent / role
        if not role_dir.is_dir():
            continue
        payload = collect(role_dir, names)
        role_manifest = role_dir / "manifest.json"
        meta = json.loads(role_manifest.read_text(encoding="utf-8")) if role_manifest.is_file() else {}
        packages[role] = {
            "dir": rel(role_dir),
            "role": meta.get("role", role.upper()),
            "purpose": meta.get("purpose", ""),
            "counts": meta.get("counts", {}),
            "files": payload,
        }
    if packages:
        manifest["handoff"]["packages"] = packages

    manifest["scripts"] = {
        name: {"sha256": sha256_file(DEFAULT_ROOT / name)} for name in SCRIPT_FILES if (DEFAULT_ROOT / name).is_file()
    }
    manifest["environment"] = {
        "python": platform.python_version(),
        "platform": platform.system(),
        "implementation": sys.implementation.name,
        "executable": rel(Path(sys.executable)),
    }
    manifest["name_cleaning"] = {
        "version": cleaning.get("version"),
        "status": "applied",
        "decisions": cleaning.get("decisions"),
        "auto_groups": cleaning.get("auto_groups"),
        "alias_mapping_pairs": cleaning.get("alias_mapping_pairs"),
        "conflict_names": cleaning.get("conflict_names"),
        "by_rule": cleaning.get("by_rule"),
        "review": {
            "groups": 1434,
            "merged_groups": 1432,
            "kept_groups": 2,
            "official_source": "腾讯应用宝（sj.qq.com）+ 必应国内版（cn.bing.com）",
            "decisions": rel(NAME_CLEANING_DIR / "review_resolution" / "review_alias_decisions.json"),
            "manual": rel(NAME_CLEANING_DIR / "review_resolution" / "review_alias_manual.json"),
        },
        "unmerged": {"hold_groups": 898},
        "summary": rel(cleaning_summary_path),
        "alias_map": rel(DEFAULT_CLEANING / "实体别名映射.json"),
        "report": rel(DEFAULT_CLEANING / "name_cleaning_report.md"),
    }
    known_issues = manifest.setdefault("known_issues", {})
    known_issues["name_cleaning_review"] = "1,434 组 review 别名未合并，保留候选供人工确认。"
    known_issues["name_cleaning_hold"] = "898 组 hold（泛化/占位/后缀差异大）不合并。"

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (person_a / "冻结说明.md").write_text(build_freeze_note(manifest, cleaning), encoding="utf-8")

    print(
        json.dumps(
            {
                "manifest": rel(manifest_path),
                "status": manifest["status"],
                "version": manifest["version"],
                "frozen_at": frozen_at,
                "handoff": manifest["handoff"]["dir"],
                "handoff_files": {name: entry["sha256"] for name, entry in manifest["handoff"]["files"].items()},
                "cleaned_files": {name: entry["sha256"] for name, entry in manifest["cleaned"].items()},
                "freeze_note": rel(person_a / "冻结说明.md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
