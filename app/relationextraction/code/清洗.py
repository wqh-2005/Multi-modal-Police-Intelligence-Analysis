import json
from pathlib import Path

input_path = Path(
    r"D:\Multi-modal-Police-Intelligence-Analysis\data\raw\诈骗案例数据集_重分类.json"
)
output_path = input_path.with_name("诈骗案例数据集_带编号.json")

with input_path.open("r", encoding="utf-8-sig") as file:
    data = json.load(file)

for index, item in enumerate(data, start=1):
    item["id"] = f"case_{index:06d}"

with output_path.open("w", encoding="utf-8") as file:
    # 与原始文件保持一致：使用2个空格缩进、多行显示。
    json.dump(data, file, ensure_ascii=False, indent=2)

print(f"已处理：{len(data)}条")
print(f"输出文件：{output_path}")
