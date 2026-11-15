"""公共回答模块（B 牵头维护，C 共用同一套调用逻辑）。

职责：把「题干 + 检索结果」变成格式 1.4 的一条回答记录。

三条硬约束：
1. 三种方法共用同一提示词、同一 ``model_id``、同一代参数（由 AnswerClient 保证）
2. ``direct`` 的 retrieval 固定为 not_applicable / 空上下文 / 空证据 / 耗时 0
3. ``cited_evidence_ids`` 必须来自本条 ``retrieval.evidence``，否则丢弃

对外接口：:func:`answer_one`。C 产出格式 1.3 后直接调用即可，
无需关心提示词与解析细节。
"""
from __future__ import annotations

import argparse

from experiments.KEO.configs import config
from experiments.KEO.core.text_rag import prompt
from experiments.KEO.models.llm_client import AnswerError, AnswerClient

# 直接回答的检索占位：规范 §5 要求 not_applicable + 空上下文 + 空证据 + 零耗时
DIRECT_RETRIEVAL: dict = {
    "status": "not_applicable",
    "context": "",
    "evidence": [],
    "elapsed_ms": 0,
    "error": None,
}


def _categories_by_length() -> list[str]:
    """长名优先匹配，避免「其它类」这类短名误命中长类别。"""
    return sorted(config.CATEGORIES, key=len, reverse=True)


def parse_category(answer_text: str) -> str | None:
    """从「类型判定：…」一行解析出 11 类之一，失败返回 None。"""
    match = prompt.CATEGORY_LINE.search(answer_text)
    if not match:
        return None
    line = match.group(1).strip()
    for category in _categories_by_length():
        if category in line:
            return category
    return None


def parse_citations(answer_text: str, allowed: set[str]) -> list[str]:
    """抽取引用编号并过滤到本条证据范围内。"""
    found: set[str] = set()
    match = prompt.CITE_LINE.search(answer_text)
    if match:
        found.update(prompt.CITE_ID.findall(match.group(1)))
    found.update(prompt.CITE_ID.findall(answer_text))
    return sorted(item for item in found if item in allowed)


def answer_one(*, run_id: str, question_id: str, question: str, question_type: str,
               method: str, retrieval: dict | None, client: AnswerClient) -> dict:
    """生成一条格式 1.4 记录。``retrieval`` 传 None 表示直接回答。"""
    retrieval = retrieval or dict(DIRECT_RETRIEVAL)
    allowed = {item["evidence_id"] for item in retrieval["evidence"]}
    prompt_text = prompt.build_prompt(question, retrieval["context"])

    try:
        raw, generation_ms = client.chat(prompt_text)
    except AnswerError as exc:
        return {
            "run_id": run_id,
            "question_id": question_id,
            "method": method,
            "model_id": client.model_id,
            "status": "error",
            "answer_text": "",
            "predicted_category": None,
            "cited_evidence_ids": [],
            "parse_status": "not_applicable",
            "retrieval": retrieval,
            "generation_elapsed_ms": 0,
            "error": {"type": exc.error_type, "message": exc.message},
        }

    if question_type == "case_judgment":
        predicted = parse_category(raw)
        parse_status = "ok" if predicted else "error"
    else:
        predicted = None
        parse_status = "not_applicable"

    return {
        "run_id": run_id,
        "question_id": question_id,
        "method": method,
        "model_id": client.model_id,
        "status": "ok",
        "answer_text": raw,
        "predicted_category": predicted,
        "cited_evidence_ids": parse_citations(raw, allowed),
        "parse_status": parse_status,
        "retrieval": retrieval,
        "generation_elapsed_ms": generation_ms,
        "error": None,
    }


# --------------------------------------------------------------- 离线自测
_SAMPLES = [
    (
        "正常回答",
        "类型判定：刷单返利类\n证据说明：E001、E003\n应对措施：1. 停止转账 2. 保存证据 3. 报警",
        {"E001", "E003"},
        "case_judgment",
    ),
    (
        "类别缺失",
        "类型判定：不知道\n证据说明：无\n应对措施：1. 报警",
        set(),
        "case_judgment",
    ),
    (
        "引用了不存在的证据",
        "类型判定：虚假贷款类\n证据说明：E001、E099\n应对措施：1. 不要转账",
        {"E001"},
        "case_judgment",
    ),
    (
        "全局题不解析类别",
        "类型判定：无\n证据说明：E001\n应对措施：1. 加强宣传",
        {"E001"},
        "global_analysis",
    ),
]


def _selftest() -> int:
    failures = 0
    for name, text, allowed, question_type in _SAMPLES:
        category = parse_category(text) if question_type == "case_judgment" else None
        cites = parse_citations(text, allowed)
        print(f"[{name}]")
        print(f"  类别 : {category!r}")
        print(f"  引用 : {cites}")

    # 断言
    assert parse_category(_SAMPLES[0][1]) == "刷单返利类"
    assert parse_category(_SAMPLES[1][1]) is None
    assert parse_citations(_SAMPLES[0][1], {"E001", "E003"}) == ["E001", "E003"]
    assert parse_citations(_SAMPLES[2][1], {"E001"}) == ["E001"], "E099 应被过滤掉"
    # 长名优先：「网络婚恋、交友类」不能被「其它类」抢先
    assert parse_category("类型判定：网络婚恋、交友类") == "网络婚恋、交友类"
    assert parse_category("类型判定：其它类") == "其它类"

    direct = dict(DIRECT_RETRIEVAL)
    assert direct["status"] == "not_applicable" and direct["evidence"] == []

    print("\n全部断言通过")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="公共回答模块的离线自测（不调用模型）")
    parser.add_argument("--selftest", action="store_true", help="运行内置断言")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
