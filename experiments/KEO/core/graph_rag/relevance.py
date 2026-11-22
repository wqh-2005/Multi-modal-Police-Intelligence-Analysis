"""有限候选的确定性词面排序；不读取类别标签、不调用模型。"""

import math
import re


def query_terms(query: str) -> list[str]:
    # 去除题目末尾统一指令，不把“诈骗类型、关键证据”当检索信号。
    text = re.split(r"请判断|请分析|请问", query or "", maxsplit=1)[0].casefold()
    terms = set(re.findall(r"[a-z][a-z0-9_-]{2,}", text))
    stop = {"对方", "受害", "害人", "报警", "警人", "事主", "随后", "之后",
            "通过", "进行", "一个", "其在", "发现", "被骗", "要求", "转账"}
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(part[i:i + 2] for i in range(len(part) - 1)
                     if part[i:i + 2] not in stop)
    return sorted(terms)


def lexical_scores(query: str, texts: list[str]) -> list[float]:
    """候选内 IDF 加权覆盖率。仅作排序分数，不解释为概率。"""
    terms = query_terms(query)
    normalized = [text.casefold() for text in texts]
    weights = {term: 1 + math.log((len(texts) + 1) /
               (1 + sum(term in text for text in normalized))) for term in terms}
    total = sum(weights.values()) or 1
    return [sum(weight for term, weight in weights.items() if term in text) / total
            for text in normalized]


def fulltext_query(query: str, max_terms: int) -> str:
    """仅发送提取的词项，禁止将原始问题作为 Lucene 查询语法。"""
    terms = query_terms(query)
    # 重复行为词优先；确定性并列排序，长度和词项数量均受限。
    text = (query or "").casefold()
    terms.sort(key=lambda term: (-text.count(term), term))
    return " OR ".join('"' + term.replace('\\', '\\\\').replace('"', '\\"') + '"'
                       for term in terms[:max_terms])
