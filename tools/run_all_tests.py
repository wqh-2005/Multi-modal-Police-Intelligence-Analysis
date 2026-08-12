#!/usr/bin/env python3
"""多模态警务智能研判系统——全项目统一测试入口。

分层（--level）：
  L0  纯逻辑单元测试（自动发现 tools/test_*.py，无外部依赖，CI 安全）
  L1  L0 + LLM 知识抽取冒烟（真实调用硅基流动，需 API key）
  L2  L1 + Neo4j 存储集成（需 Neo4j 运行在 7687）
  L3  L2 + 服务级端到端（需 FastAPI 服务运行在 8000）
  all 等价于 L3

用法：
  python tools/run_all_tests.py --level L0            # 纯逻辑（推荐 CI）
  python tools/run_all_tests.py --level L0 --report tools/test_report.json
  python tools/run_all_tests.py --level all           # 全量（自动跳过不可用外部依赖）
  python tools/run_all_tests.py --skip-llm            # 旧参数兼容（等价 --level L0）
  python tools/run_all_tests.py -v                    # 详细输出

退出码：全部通过=0，任一失败=1。
"""
import argparse
import asyncio
import json
import os
import socket
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(ROOT / ".paddlex-cache"))

ENV_FILE = ROOT / ".env"
NEEDED_ENV_KEYS = [
    "SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL",
    "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
    "JUDGMENT_API_KEY", "JUDGMENT_BASE_URL", "RAG_TOP_K", "LLM_MAX_TOKENS",
    "LLMMODEL", "RAGENGING_MODEL", "EXAMPLE_JSON_PATH",
    "JSON_PROCESSED", "RAG_COLLECTION",
]
SERVICE_URL = "http://127.0.0.1:8000"


def section(title: str):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def discover_unit_modules() -> list[str]:
    """自动发现 tools/test_*.py 为 unittest 模块。"""
    modules = []
    for f in sorted((ROOT / "tools").glob("test_*.py")):
        modules.append(f"tools.{f.stem}")
    return modules


def check_env() -> tuple[bool, str]:
    """检查 .env 必需字段与关键文件。"""
    section("A. 环境完整性检查")
    if not ENV_FILE.exists():
        return False, ".env 不存在（请复制 .env.example 并配置）"
    missing = []
    with open(ENV_FILE, encoding="utf-8") as f:
        content = f.read()
    placeholders = (
        "your_", "模板", "占位", "链接", "MODAL_TYPE", "MODEL_TYPE", "YOUR API",
        "生成的数据放在哪里", "集合名称", "外挂知识库", "你的模板", "大模型",
    )
    for key in NEEDED_ENV_KEYS:
        for line in content.splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                val = line.split("=", 1)[1].strip().strip('"')
                if val and all(p not in val for p in placeholders):
                    break
        else:
            missing.append(key)
    if missing:
        return False, f".env 缺少或为占位值: {', '.join(missing)}"
    if not (ROOT / "system_prompt.txt").exists():
        return False, "system_prompt.txt 不存在"
    print(f"  ✓ .env 必需字段齐全（{len(NEEDED_ENV_KEYS)} 项）")
    print("  ✓ system_prompt.txt 存在")
    return True, ""


def run_unit(verbose: bool) -> tuple[int, int, list[str]]:
    """运行自动发现的纯逻辑单元测试。"""
    section("B. 纯逻辑单元测试（自动发现 tools/test_*.py）")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    failures = []
    for m in discover_unit_modules():
        try:
            suite.addTests(loader.loadTestsFromName(m))
        except Exception as e:  # noqa: BLE001
            failures.append(f"无法加载 {m}: {e}")
            print(f"  ✗ 无法加载 {m}: {e}")
    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1, stream=sys.stdout)
    result = runner.run(suite)
    failures += [str(f[0]) for f in result.failures]
    failures += [str(e[0]) for e in result.errors]
    return result.testsRun, len(result.failures) + len(result.errors), failures


async def smoke_llm() -> tuple[bool, str]:
    """模块二知识抽取真实 LLM 冒烟。"""
    section("C. LLM 知识抽取冒烟（真实调用硅基流动）")
    from app.core.knowledge.extraction_service import run_extraction
    text = ("报案人经人介绍认识了自称股票专家的案犯甲，案犯甲将其拉入微信群，称可投资电影赚钱。"
            "报案人信以为真，通过网银向案犯甲提供的账户转账66000元。后联系不上案犯甲，意识到被骗。")
    try:
        result = await run_extraction(text, case_id="tools-smoke-llm")
        ok = len(result.triplets) > 0
        print(f"  {'✓' if ok else '✗'} 抽取到 {len(result.triplets)} 条三元组，置信度 {result.extraction_confidence}")
        return ok, ""
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def smoke_neo4j() -> tuple[bool, str]:
    """Neo4j 连通性集成冒烟。"""
    section("D. Neo4j 存储集成")
    if not port_open("localhost", 7687):
        print("  - 跳过：Neo4j 未运行（7687 未监听）")
        return True, "skipped"
    from app.core.knowledge.storage_service import _get_driver
    try:
        d = _get_driver()
        await d.verify_connectivity()
        print("  ✓ Neo4j 连接成功")
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def smoke_service() -> tuple[bool, str]:
    """服务级端到端（探测可用端点）。"""
    section("E. 服务级端到端")
    if not port_open("127.0.0.1", 8000):
        print("  - 跳过：FastAPI 服务未运行（8000 未监听）")
        return True, "skipped"
    import httpx
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            paths = (await c.get(f"{SERVICE_URL}/openapi.json")).json()["paths"]
            payload = {
                "case_id": "tools-smoke-service",
                "inputs": [{
                    "type": "text",
                    "content": "报案人接到自称公检法的电话，被诱导向安全账户转账20万元后发现被骗。",
                }],
            }
            if "/api/v1/pipeline" in paths:
                r = await c.post(f"{SERVICE_URL}/api/v1/pipeline", json=payload)
            elif "/multimodal/analyze" in paths:
                r = await c.post(f"{SERVICE_URL}/multimodal/analyze", json=payload)
            else:
                return False, "服务无可用测试端点"
            ok = r.status_code == 200
            print(f"  {'✓' if ok else '✗'} {r.request.url.path} -> HTTP {r.status_code}")
            if ok:
                data = r.json()
                if isinstance(data, dict) and "elapsed_ms" in data:
                    try:
                        print(f"     耗时 {float(data['elapsed_ms']):.0f}ms，阶段: {list(data.get('stages', {}).keys())}")
                    except (TypeError, ValueError):
                        print(f"     响应: {str(data)[:200]}")
            else:
                print(f"     响应: {r.text[:200]}")
            return ok, "" if ok else f"HTTP {r.status_code}: {r.text[:150]}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


async def main():
    parser = argparse.ArgumentParser(description="多模态警务智能研判系统统一测试入口")
    parser.add_argument("--level", choices=["L0", "L1", "L2", "L3", "all"], default="all",
                        help="测试层级（默认 all=L3）")
    parser.add_argument("--report", default="", help="JSON 报告输出路径（如 tools/test_report.json）")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 LLM 冒烟（等价 --level L0）")
    parser.add_argument("--skip-neo4j", action="store_true", help="跳过 Neo4j 集成")
    parser.add_argument("--skip-service", action="store_true", help="跳过服务级测试")
    parser.add_argument("--unit-only", action="store_true", help="只跑纯逻辑单元测试（等价 --level L0）")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    # 层级解析（旧参数兼容）
    if args.unit_only:
        level = 0
    elif args.level == "L0":
        level = 0
    elif args.level == "L1":
        level = 1
    elif args.level == "L2":
        level = 2
    else:  # L3 / all
        level = 3
    # 注：--skip-llm 仅跳过 C 阶段（见下），不影响 D/E 层级

    results = []  # (阶段, ok, 说明)
    failures_all: list[str] = []

    # A. 环境检查
    ok, msg = check_env()
    results.append(("A 环境", ok, msg))
    if not ok:
        print(f"  ✗ {msg}")
        print("\n环境不完整，仅继续运行纯逻辑单元测试。")

    # B. 单元测试（始终运行）
    run, failed, unit_failures = run_unit(args.verbose)
    results.append(("B 单元", failed == 0, f"{run - failed}/{run} 通过"))
    failures_all += unit_failures

    # C. LLM 冒烟（L1+）
    if level >= 1 and not args.skip_llm:
        ok, msg = await smoke_llm()
        results.append(("C LLM", ok, msg))
        if not ok:
            print(f"  ✗ {msg}")
    else:
        results.append(("C LLM", True, "skipped"))

    # D. Neo4j 集成（L2+）
    if level >= 2 and not args.skip_neo4j:
        ok, msg = await smoke_neo4j()
        results.append(("D Neo4j", ok, msg))
    else:
        results.append(("D Neo4j", True, "skipped"))

    # E. 服务级（L3+）
    if level >= 3 and not args.skip_service:
        ok, msg = await smoke_service()
        results.append(("E 服务", ok, msg))
    else:
        results.append(("E 服务", True, "skipped"))

    return finish(results, failures_all, args.report, level)


def finish(results: list, failures: list, report_path: str, level: int) -> int:
    print(f"\n{'=' * 60}\n  测试报告（层级 L{level}）\n{'=' * 60}")
    all_ok = True
    report = {
        "timestamp": datetime.now().isoformat(),
        "level": f"L{level}",
        "summary": {"total": len(results), "passed": 0, "failed": 0, "skipped": 0},
        "stages": {},
        "failures": failures,
    }
    for name, ok, msg in results:
        status = "✓" if ok else "✗"
        all_ok &= ok
        print(f"  [{status}] {name:<8} {msg}")
        stage = {"ok": ok, "detail": msg}
        if "skipped" in str(msg):
            report["summary"]["skipped"] += 1
        elif ok:
            report["summary"]["passed"] += 1
        else:
            report["summary"]["failed"] += 1
        report["stages"][name] = stage

    print(f"\n  结论: {'全部通过' if all_ok else '存在失败'}")
    if report_path:
        p = ROOT / report_path
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  JSON 报告已写入: {p}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
