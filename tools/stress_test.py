#!/usr/bin/env python3
"""三模块链路冒烟 + 压力测试脚本。

覆盖链路:
    1. 健康检查        GET  /api/v1/knowledge/health          （模块二/三 + Neo4j）
    2. 文本多模态      POST /multimodal/analyze               （模块一, text 直通）
    3. 端到端流水线    POST /api/v1/knowledge/pipeline        （模块二抽取 + 模块三存储）
    4. 图片 OCR        POST /multimodal/analyze               （模块一, PaddleX OCR, 可选 --include-ocr）

用法:
    # 冒烟（默认）: 每类各 1 次
    python tools/stress_test.py

    # 冒烟 + OCR
    python tools/stress_test.py --include-ocr

    # 并发压测: analyze 与 pipeline 各 3 并发 × 5 轮
    python tools/stress_test.py --concurrency 3 --rounds 5

    # 只跑健康检查
    python tools/stress_test.py --smoke-only

    # 自定义服务地址
    python tools/stress_test.py --base-url http://127.0.0.1:8000 --concurrency 4 --rounds 3

注意:
    - pipeline 每轮会真实调用 LLM（硅基流动）并写入 Neo4j，注意 API 配额。
    - OCR 每次推理约 1~3 秒（CPU），压测时请调低并发。
    - 测试数据会写入 Neo4j（case_id 带 stress 前缀），可用 cypher 清理。
"""
import argparse
import asyncio
import base64
import io
import json
import random
import statistics
import sys
import time
from pathlib import Path

import httpx

# 项目根（脚本位于 tools/ 下）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 冒烟/压测用的案件文本（真实诈骗案件叙述，长度适中）
CASE_TEXTS = [
    "报案人经人介绍认识了自称股票专家的案犯甲，案犯甲将其拉入微信群，称可投资电影赚钱。"
    "报案人信以为真，通过网银向案犯甲提供的账户转账66000元。后联系不上案犯甲，意识到被骗。",
    "报警人称在QQ上添加了一陌生好友，对方以刷单返利为由诱导其下载APP，"
    "报警人按指示向对方指定账户转账共计12万元，后无法提现发现被骗。",
    "受害者王某接到自称公检法工作人员的电话，称其涉嫌洗钱需配合调查，"
    "王某按对方要求将名下存款20万元转入「安全账户」，后发现被骗报警。",
]

# 供 OCR 测试的图片文本
OCR_TEXT = "报案人称被诈骗66000元"


def build_text_request(case_id: str, text: str) -> dict:
    """格式 1.1：text 输入（/multimodal/analyze 用）。"""
    return {"case_id": case_id, "inputs": [{"type": "text", "content": text}]}


def build_f12_text_request(case_id: str, text: str) -> dict:
    """格式 1.2：text 多模态输出（/api/v1/knowledge/pipeline 用）。"""
    return {
        "case_id": case_id,
        "outputs": [{"type": "text", "text": text, "status": "done"}],
    }


def build_ocr_image() -> bytes:
    """用 PIL 生成一张含中文的测试图片（系统 Noto CJK 字体）。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (800, 200), "white")
    draw = ImageDraw.Draw(img)
    font_paths = [
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    font = None
    for fp in font_paths:
        if Path(fp).exists():
            font = ImageFont.truetype(fp, 40)
            break
    if font is None:
        font = ImageFont.load_default()
    draw.text((50, 70), OCR_TEXT, font=font, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_image_request(case_id: str, png: bytes) -> dict:
    """格式 1.1：image 输入（base64）。"""
    b64 = base64.b64encode(png).decode()
    return {"case_id": case_id, "inputs": [{"type": "image", "content": f"data:image/png;base64,{b64}"}]}


async def call(client: httpx.AsyncClient, method: str, url: str, json_body: dict, timeout: float = 120) -> tuple:
    """发起单次请求，返回 (ok, elapsed_ms, detail)。"""
    t0 = time.perf_counter()
    try:
        resp = await client.request(method, url, json=json_body, timeout=timeout)
        elapsed = (time.perf_counter() - t0) * 1000
        ok = resp.status_code == 200
        detail = ""
        if ok:
            try:
                data = resp.json()
                detail = summarize(data)
            except Exception:
                detail = ""
        else:
            detail = f"HTTP {resp.status_code}: {resp.text[:120]}"
        return ok, round(elapsed, 1), detail
    except Exception as e:
        return False, 0.0, f"{type(e).__name__}: {e}"


def summarize(data: dict) -> str:
    """提取响应关键信息用于输出。"""
    parts = []
    if "outputs" in data:  # 格式 1.2
        for o in data.get("outputs", []):
            if o.get("status") == "done":
                t = o.get("text") or ""
                parts.append(f"[{o['type']}] conf={o.get('confidence')} {t[:24]}")
    elif "triplets" in data:  # 格式 1.3
        parts.append(f"triplets={len(data['triplets'])}")
    elif "relations" in data:  # 格式 1.4
        v = data.get("victim", {}).get("name")
        s = data.get("suspect", {}).get("name")
        parts.append(f"victim={v} suspect={s} rels={len(data.get('relations', []))}")
    return " | ".join(parts)


def fmt_stats(name: str, results: list[tuple]):
    """汇总统计: 成功率 / 平均 / P50 / P95 / 总耗时。"""
    if not results:
        return
    ok = sum(1 for r in results if r[0])
    lat = sorted(r[1] for r in results)
    p50 = statistics.median(lat) if lat else 0
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0
    avg = sum(lat) / len(lat) if lat else 0
    total = sum(r[1] for r in results)
    print(f"  {name:<14} 成功 {ok}/{len(results)}  平均 {avg:.0f}ms  P50 {p50:.0f}ms  "
          f"P95 {p95:.0f}ms  累计 {total/1000:.1f}s")


async def run_smoke(client: httpx.AsyncClient, base: str, include_ocr: bool) -> bool:
    """冒烟测试：每类接口各打 1 次，全部成功返回 True。"""
    print("\n========== 冒烟测试 ==========")
    all_ok = True

    # 1. 健康检查
    ok, ms, _ = await call(client, "GET", f"{base}/api/v1/knowledge/health", {})
    all_ok &= ok
    print(f"  [health]           {'PASS' if ok else 'FAIL'}  {ms:.0f}ms")

    # 2. text 多模态
    ok, ms, detail = await call(client, "POST", f"{base}/multimodal/analyze",
                                build_text_request("stress-smoke-1", CASE_TEXTS[0]))
    all_ok &= ok
    print(f"  [analyze-text]     {'PASS' if ok else 'FAIL'}  {ms:.0f}ms  {detail}")

    # 3. 端到端流水线（抽取+存储，真实 LLM + Neo4j；输入为格式 1.2）
    ok, ms, detail = await call(client, "POST", f"{base}/api/v1/knowledge/pipeline",
                                build_f12_text_request("stress-smoke-2", CASE_TEXTS[1]))
    all_ok &= ok
    print(f"  [pipeline]         {'PASS' if ok else 'FAIL'}  {ms:.0f}ms  {detail}")

    # 4. 图片 OCR（可选）
    if include_ocr:
        png = build_ocr_image()
        ok, ms, detail = await call(client, "POST", f"{base}/multimodal/analyze",
                                    build_image_request("stress-smoke-3", png))
        all_ok &= ok
        print(f"  [analyze-image]    {'PASS' if ok else 'FAIL'}  {ms:.0f}ms  {detail}")

    print("========== 冒烟结果: " + ("全部通过 ✓" if all_ok else "存在失败 ✗") + " ==========")
    return all_ok


async def run_load(client: httpx.AsyncClient, base: str, concurrency: int, rounds: int, include_ocr: bool):
    """压力测试：并发 × 轮次，打 analyze(text) 与 pipeline。"""
    print(f"\n========== 压力测试（并发 {concurrency} × 轮次 {rounds}）==========")
    sem = asyncio.Semaphore(concurrency)

    async def worker(label: str, url: str, maker):
        results = []
        async with sem:
            for i in range(rounds):
                case_id = f"stress-{label}-{random.randint(10000, 99999)}"
                body = maker(case_id)
                results.append(await call(client, "POST", url, body))
        return results

    # analyze(text)：不调 LLM，纯粹接口压力
    texts = [CASE_TEXTS[i % len(CASE_TEXTS)] for i in range(concurrency)]
    t0 = time.perf_counter()
    analyze_results = await asyncio.gather(*[
        worker("analyze", f"{base}/multimodal/analyze",
               lambda cid, t=t: build_text_request(cid, t))
        for t in texts
    ])
    flat_a = [r for batch in analyze_results for r in batch]
    fmt_stats("analyze(text)", flat_a)
    print(f"  吞吐: {len(flat_a) / max(time.perf_counter() - t0, 1e-6):.1f} req/s")

    # pipeline：真实 LLM + Neo4j，注意 API 配额
    t0 = time.perf_counter()
    pipe_results = await asyncio.gather(*[
        worker("pipeline", f"{base}/api/v1/knowledge/pipeline",
               lambda cid, t=t: build_f12_text_request(cid, t))
        for t in texts
    ])
    flat_p = [r for batch in pipe_results for r in batch]
    fmt_stats("pipeline", flat_p)
    print(f"  吞吐: {len(flat_p) / max(time.perf_counter() - t0, 1e-6):.1f} req/s")

    if include_ocr:
        png = build_ocr_image()
        ocr_results = await asyncio.gather(*[
            worker("ocr", f"{base}/multimodal/analyze", lambda cid: build_image_request(cid, png))
            for _ in range(min(concurrency, 3))  # OCR 是 CPU 密集，最多 3 并发
        ])
        flat_o = [r for batch in ocr_results for r in batch]
        fmt_stats("analyze(image)", flat_o)

    print("========== 压力测试结束 ==========")


async def main():
    parser = argparse.ArgumentParser(description="多模态警务系统三模块冒烟/压力测试")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="服务地址（默认 http://127.0.0.1:8000）")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数（默认 3）")
    parser.add_argument("--rounds", type=int, default=3, help="每并发轮次（默认 3）")
    parser.add_argument("--smoke-only", action="store_true", help="只跑冒烟测试")
    parser.add_argument("--include-ocr", action="store_true", help="包含图片 OCR 测试（较慢）")
    args = parser.parse_args()

    # 先探活
    async with httpx.AsyncClient() as c:
        try:
            r = await c.get(f"{args.base_url}/docs", timeout=5)
            if r.status_code != 200:
                print(f"服务未就绪: {args.base_url} HTTP {r.status_code}")
                sys.exit(1)
        except Exception as e:
            print(f"无法连接服务 {args.base_url}: {e}")
            print("请先启动服务，例如: bash run.sh 或 uvicorn app.main:app")
            sys.exit(1)

        print(f"服务在线: {args.base_url}")
        smoke_ok = await run_smoke(c, args.base_url, args.include_ocr)
        if not args.smoke_only and smoke_ok:
            await run_load(c, args.base_url, args.concurrency, args.rounds, args.include_ocr)

    sys.exit(0 if smoke_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
