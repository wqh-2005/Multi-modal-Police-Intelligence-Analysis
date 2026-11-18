"""节点向量化进度查看器：每 5 秒刷新一次，只读，不写任何数据。

用法（仓库根执行，或双击 tools\\看进度.cmd）：

    .\\.venv\\Scripts\\python.exe experiments\\KEO\\tools\\watch_progress.py

按 Ctrl+C 退出。状态变为 ready 时自动结束。
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

import chromadb  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

KEO = os.path.join(REPO, "experiments", "KEO")
VECTOR_DIR = os.path.join(KEO, "output", "test", "node_vectors")
COLLECTION = "keo_graph_nodes"
REFRESH = 5


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def bar(done: int, total: int, width: int = 34) -> str:
    ratio = done / total if total else 0
    filled = int(width * ratio)
    return "[" + "#" * filled + "." * (width - filled) + f"] {ratio * 100:5.1f}%"


def main() -> int:
    driver = GraphDatabase.driver(
        os.getenv("KEO_NEO4J_URI"),
        auth=(os.getenv("KEO_NEO4J_USER"), os.getenv("KEO_NEO4J_PASSWORD")),
    )
    try:
        client = chromadb.PersistentClient(path=VECTOR_DIR)
        collection = client.get_or_create_collection(name=COLLECTION)
        chroma_ok = True
    except Exception:
        collection = None
        chroma_ok = False

    total = 0
    started = time.time()
    first_done = None
    last_done = None
    last_at = None
    rate = 0.0
    once = "--once" in sys.argv          # 只打印一次，便于脚本调用与自检

    while True:
        status, error, facts, rels = "unknown", "", 0, 0
        try:
            with driver.session() as session:
                row = session.run(
                    "MATCH (s:KEOKnowledgeBase) RETURN s.status AS st, s.error_message AS err"
                ).single()
                if row:
                    status = row["st"] or "unknown"
                    error = row["err"] or ""
                counts = session.run(
                    "MATCH (f:KEOFact) WITH count(f) AS facts "
                    "MATCH ()-[r]->() RETURN facts, count(r) AS rels"
                ).single()
                if counts:
                    facts, rels = counts["facts"], counts["rels"]
            if not total:
                with driver.session() as session:
                    total = session.run("MATCH (e:KEOEntity) RETURN count(e) AS c").single()["c"]
        except Exception as exc:  # noqa: BLE001 - 查看器不能因为查询失败就崩
            error = str(exc)[:80]

        done = collection.count() if chroma_ok else 0
        now = time.time()
        if first_done is None:
            first_done = done
        if last_done is not None and now > last_at:
            instant = (done - last_done) / (now - last_at)
            rate = instant if rate == 0 else rate * 0.7 + instant * 0.3
        last_done, last_at = done, now

        remaining = max(total - done, 0)
        eta = remaining / rate / 60 if rate > 0.5 else None
        elapsed = (now - started) / 60

        clear()
        print("=" * 62)
        print("  KEO 节点向量化进度")
        print("=" * 62)
        print(f"  时间        {datetime.now():%H:%M:%S}    已观察 {elapsed:.1f} 分钟")
        print(f"  知识库状态  {status}" + (f"    错误: {error[:50]}" if status == "error" else ""))
        print()
        print(f"  {bar(done, total)}")
        print(f"  已向量化    {done:,} / {total:,}")
        print(f"  当前速率    {rate:.1f} 节点/秒")
        print(f"  预计剩余    " + (f"{eta:.0f} 分钟（约 {eta / 60:.1f} 小时）" if eta else "正在测速…"))
        print()
        print(f"  图规模      事实 {facts:,}   关系 {rels:,}")

        if not chroma_ok:
            print("\n  [提示] 向量库尚未创建，索引可能还没开始写")
        if status == "ready":
            print("\n  知识库已就绪，可以开始 graph_rag 检索了。")
            return 0
        if status == "error":
            print("\n  [提示] 状态为 error：需要重跑 node_index --resume，")
            print("         或运行 tools\\node_index_supervisor.py 自动续跑。")

        if once:
            return 0
        time.sleep(REFRESH)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已退出。")
