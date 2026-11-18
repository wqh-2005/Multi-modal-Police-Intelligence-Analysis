"""节点向量化守护：自动续跑，直到知识库就绪。

为什么需要：461,292 个节点要跑数小时，中间只要断一次网，node_index 就会
以 APIConnectionError 退出并把知识库置为 error。人工反复重跑不现实。

本脚本做的事：
1. 跑 node_index --resume；
2. 退出后检查知识库状态：ready 就结束；
3. 若为 error，把它复位成 updating 再重试（否则 --resume 会被拒绝）；
4. 失败间隔指数退避，最多 20 次。

用法（仓库根执行）：

    .\\.venv\\Scripts\\python.exe experiments\\KEO\\tools\\node_index_supervisor.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

from neo4j import GraphDatabase  # noqa: E402

MAX_ATTEMPTS = 20
PYTHON = sys.executable


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def read_status(driver) -> tuple[str, str]:
    try:
        with driver.session() as session:
            row = session.run(
                "MATCH (s:KEOKnowledgeBase) RETURN s.status AS st, s.error_message AS err"
            ).single()
            if row:
                return row["st"] or "unknown", row["err"] or ""
    except Exception as exc:  # noqa: BLE001
        return "unreachable", str(exc)[:120]
    return "missing", ""


def reset_to_updating(driver) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (s:KEOKnowledgeBase) SET s.status='updating' REMOVE s.error_message"
        )


def main() -> int:
    driver = GraphDatabase.driver(
        os.getenv("KEO_NEO4J_URI"),
        auth=(os.getenv("KEO_NEO4J_USER"), os.getenv("KEO_NEO4J_PASSWORD")),
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        status, error = read_status(driver)
        log(f"第 {attempt} 次启动：当前状态 {status}" + (f"（{error[:60]}）" if error else ""))
        if status == "ready":
            log("知识库已就绪，退出守护")
            return 0
        if status == "error":
            reset_to_updating(driver)
            log("状态已复位为 updating")

        proc = subprocess.run(
            [PYTHON, "-u", "-m", "experiments.KEO.core.graph_rag.node_index", "--resume"],
            cwd=REPO,
        )
        status, error = read_status(driver)
        log(f"本次退出码 {proc.returncode}，状态 {status}"
            + (f"（{error[:60]}）" if error else ""))
        if status == "ready":
            log("知识库已就绪，退出守护")
            return 0

        wait = min(30 * attempt, 300)
        log(f"{wait} 秒后重试")
        time.sleep(wait)

    log(f"已达最大重试次数 {MAX_ATTEMPTS}，请人工检查")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
