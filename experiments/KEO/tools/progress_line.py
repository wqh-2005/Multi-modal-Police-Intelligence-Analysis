"""打印一行式进度，供反复调用（配合 watch.ps1 或手动 while 循环）。

用法：
    .\\.venv\\Scripts\\python.exe experiments\\KEO\\tools\\progress_line.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

import chromadb  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

TEST = os.path.join(REPO, "experiments", "KEO", "output", "test")
TARGETS = [
    ("主库", "node_vectors", "keo_graph_nodes", "neo4j"),
    ("沙盒", "node_vectors_smoke", "keo_smoke_nodes", "keosmoke"),
]


def bar(done: int, total: int, width: int = 24) -> str:
    if not total:
        return "[" + "." * width + "]"
    filled = int(width * done / total)
    return "[" + "#" * filled + "." * (width - filled) + f"] {done / total * 100:5.1f}%"


def main() -> int:
    print(f"KEO 进度  {datetime.now():%H:%M:%S}")
    print("-" * 54)
    try:
        driver = GraphDatabase.driver(
            os.getenv("KEO_NEO4J_URI"),
            auth=(os.getenv("KEO_NEO4J_USER"), os.getenv("KEO_NEO4J_PASSWORD")),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Neo4j 连接失败：{str(exc)[:60]}")
        return 1

    for label, subdir, collection, database in TARGETS:
        path = os.path.join(TEST, subdir)
        if not os.path.isdir(path):
            print(f"{label}  尚未开始")
            continue
        try:
            client = chromadb.PersistentClient(path=path)
            done = client.get_or_create_collection(name=collection).count()
        except Exception:
            done = 0
        try:
            with driver.session(database=database) as session:
                entities = session.run(
                    "MATCH (e:KEOEntity) RETURN count(e) AS c").single()["c"]
                kb = session.run(
                    "MATCH (s:KEOKnowledgeBase) RETURN s.status AS st").single()
                status = kb["st"] if kb else "missing"
        except Exception as exc:  # noqa: BLE001
            entities, status = 0, f"查询失败 {str(exc)[:30]}"
        print(f"{label}  {bar(done, entities)}  {done:,}/{entities:,}  状态={status}")

    driver.close()
    print("-" * 54)
    print("状态 ready 表示该库可检索")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
