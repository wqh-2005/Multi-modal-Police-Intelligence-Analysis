"""依据已保存的扩充记录复现600篇测试输入。

默认只检查来源与选择清单；传入 --apply 才会覆盖 datas/test 下的当前测试文件
和新增400目录。这样可以避免误操作再次随机抽样。
"""

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = PROJECT_ROOT / "datas" / "test"
SOURCE_DIR = PROJECT_ROOT / "datas" / "code"
BACKUP_DIR = TEST_DIR / "backup_before_expand_20260923"
DELTA_DIR = TEST_DIR / "新增400"
MANIFEST_PATH = TEST_DIR / "扩充记录.json"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dialogue_items(value: Any) -> list[dict[str, Any]]:
    return value.get("items", value) if isinstance(value, dict) else value


def case_items(value: Any) -> list[dict[str, Any]]:
    return value.get("items", []) if isinstance(value, dict) else value


def main() -> None:
    parser = argparse.ArgumentParser(description="按扩充记录复现300篇对话和300篇案例")
    parser.add_argument("--apply", action="store_true", help="确认后写入测试数据文件")
    args = parser.parse_args()

    manifest = read_json(MANIFEST_PATH)
    all_dialogues_raw = read_json(SOURCE_DIR / "对话数据集.json")
    all_dialogues = dialogue_items(all_dialogues_raw)
    all_cases = case_items(read_json(SOURCE_DIR / "诈骗案例数据集_重分类.json"))
    old_dialogues_raw = read_json(BACKUP_DIR / "对话测试.json")
    old_dialogues = dialogue_items(old_dialogues_raw)
    old_cases = case_items(read_json(BACKUP_DIR / "案例测试.json"))

    dialogue_map = {str(item["id"]): item for item in all_dialogues}
    selected_dialogues = [dialogue_map[source_id] for source_id in manifest["new_dialogue_ids"]]
    # 清单中的案例索引采用从1开始计数。
    selected_cases = [all_cases[index - 1] for index in manifest["new_case_source_indexes"]]

    if len(old_dialogues) != 100 or len(old_cases) != 100:
        raise ValueError("原始备份不是100篇对话加100篇案例")
    if len(selected_dialogues) != 200 or len(selected_cases) != 200:
        raise ValueError("扩充清单不是200篇对话加200篇案例")
    if len({item["id"] for item in selected_dialogues}) != 200:
        raise ValueError("新增对话ID不唯一")
    if set(item["id"] for item in old_dialogues) & set(item["id"] for item in selected_dialogues):
        raise ValueError("新增对话与原100篇重复")

    standard_categories = (
        all_dialogues_raw.get("standard_categories", [])
        if isinstance(all_dialogues_raw, dict) else []
    )
    merged_dialogues = {
        "standard_categories": standard_categories,
        "items": old_dialogues + selected_dialogues,
    }
    delta_dialogues = {
        "standard_categories": standard_categories,
        "items": selected_dialogues,
    }
    merged_cases = old_cases + selected_cases

    summary = {
        "seed": manifest["seed"],
        "strategy": manifest["strategy"],
        "dialogues": len(merged_dialogues["items"]),
        "cases": len(merged_cases),
        "new_dialogues": len(selected_dialogues),
        "new_cases": len(selected_cases),
        "mode": "write" if args.apply else "check_only",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.apply:
        return

    write_json(TEST_DIR / "对话测试.json", merged_dialogues)
    write_json(TEST_DIR / "案例测试.json", merged_cases)
    write_json(DELTA_DIR / "新增对话200.json", delta_dialogues)
    write_json(DELTA_DIR / "新增案例200.json", selected_cases)


if __name__ == "__main__":
    main()
