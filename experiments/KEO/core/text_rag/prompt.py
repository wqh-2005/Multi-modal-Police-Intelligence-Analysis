"""唯一的提示词模板。

三种方法（direct / text_rag / graph_rag）共用同一套任务要求与输出格式，
只有 ``context`` 不同。**修改本文件会同时影响三组回答，必须先沟通。**

输出被强制为三行，是为了让 ``answerer.py`` 能把自由文本解析成可评分的
``predicted_category`` 与 ``cited_evidence_ids``。
"""
from __future__ import annotations

import argparse
import re

from experiments.KEO.configs import config

_TEMPLATE = """你是反诈研判助手。请阅读案情，完成三件事：判断诈骗类型、指出证据、提出应对措施。

【诈骗类型（必须从下列选项中选一个，原样输出）】
{categories}

【输出格式（严格照此三行，不要额外说明）】
类型判定：<上面 11 类中的一个>
证据说明：<用到的资料编号，如 E001、E003；没有资料就写“无”>
应对措施：<1. … 2. … 3. …>

【案情】
{question}

【参考资料】
{context}
"""

# 解析用正则，供 answerer.py 使用
CATEGORY_LINE = re.compile(r"类型判定\s*[：:]\s*(.+)")
CITE_LINE = re.compile(r"证据说明\s*[：:]\s*(.+)")
CITE_ID = re.compile(r"E\d{3,}")


def build_prompt(question: str, context: str) -> str:
    """拼装提示词；context 为空时给出明确的「无资料」说明。"""
    categories = "\n".join(
        f"{index}. {name}" for index, name in enumerate(config.CATEGORIES, start=1)
    )
    return _TEMPLATE.format(
        categories=categories,
        question=(question or "").strip(),
        context=(context or "").strip() or "（无参考资料，请仅依据案情判断）",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="查看提示词渲染结果")
    parser.add_argument("--with-context", action="store_true", help="带上下文的版本")
    args = parser.parse_args()

    question = "对方称刷单可以赚钱，并要求下载应用充值。请判断诈骗类型、指出证据并提出应对措施。"
    context = (
        "[E001] 案例case_000002中，对方要求受害人下载刷单APP并转账。\n"
        "[E002] 案例case_000005中，刷单返利需先充值再提现。"
    ) if args.with_context else ""

    rendered = build_prompt(question, context)
    print("=" * 70)
    print(rendered)
    print("=" * 70)
    print(f"提示词长度 {len(rendered)} 字，类别 {len(config.CATEGORIES)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
