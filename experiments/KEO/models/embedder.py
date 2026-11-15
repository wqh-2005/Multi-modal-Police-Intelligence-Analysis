"""Embedding 封装（默认 BAAI/bge-m3）。

刻意不 import ``app.models.SiliconFlowEmbedding``：KEO 是独立实验，
必须与主项目解耦，否则主项目改动会破坏实验可复现性。
"""
from __future__ import annotations

import argparse
import time
from typing import Sequence

from openai import APIConnectionError, BadRequestError, OpenAI

from experiments.KEO.configs import config
from experiments.KEO.models.http_client import make_http_client


class Embedder:
    """OpenAI 兼容协议的 embedding 客户端。"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None):
        self._client = OpenAI(
            api_key=api_key or config.require_api_key(),
            base_url=base_url or config.BASE_URL,
            http_client=make_http_client(),   # 忽略系统代理，避免国内接口被绕
            max_retries=config.MAX_RETRIES,
        )
        self._model = model or config.EMBEDDING_MODEL
        self._dim: int | None = None
        self._max_chars = 1200          # 单条兜底截断长度，只在接口报 400 时使用

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int | None:
        """向量维度，首次调用后才有值。"""
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量向量化。

        返回顺序必须与输入顺序一致：接口按 ``index`` 返回，**不排序会静默串位**，
        表现为检索结果看似正常但全部错位，是最难排查的一类错误。
        """
        items = list(texts)
        if not items:
            return []

        response = self._embed_with_retry(items)
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [item.embedding for item in ordered]

        if len(vectors) != len(items):
            raise RuntimeError(f"向量数量与输入不符：{len(vectors)} != {len(items)}")
        if self._dim is None:
            self._dim = len(vectors[0])
        return vectors

    def _embed_with_retry(self, items: list[str], attempt: int = 1):
        """两种兜底，目的都是别让一次异常打断几十分钟的建索引。

        1. 返回 400（常见于单次请求 token 超限）：对半拆分重试；
        2. 连接失败：退避重试，最多 3 次。
        """
        try:
            return self._client.embeddings.create(model=self._model, input=items)
        except APIConnectionError as exc:
            if attempt >= 3:
                raise
            time.sleep(2 ** attempt)
            return self._embed_with_retry(items, attempt + 1)
        except BadRequestError as exc:
            if len(items) > 1:
                middle = len(items) // 2
                first = self._embed_with_retry(items[:middle])
                second = self._embed_with_retry(items[middle:])
                return _MergedEmbedding(first.data, second.data)
            trimmed = items[0][: self._max_chars]
            if trimmed and len(trimmed) < len(items[0]):
                return self._client.embeddings.create(model=self._model, input=[trimmed])
            raise RuntimeError(
                f"embedding 被拒且无法再拆分：单条 {len(items[0])} 字符。原始错误：{exc}"
            ) from exc

    def embed_one(self, text: str) -> list[float]:
        """单条向量化，检索时使用。"""
        return self.embed([text])[0]


class _MergedEmbedding:
    """把两次 embedding 的 data 拼在一起，让上层按统一方式处理。"""

    def __init__(self, first, second):
        self.data = list(first) + list(second)


def main() -> int:
    parser = argparse.ArgumentParser(description="embedding 连通性自测")
    parser.add_argument("--live", action="store_true", help="真正调用一次接口")
    args = parser.parse_args()

    print(f"模型   : {config.EMBEDDING_MODEL}")
    print(f"地址   : {config.BASE_URL}")
    print(f"密钥   : {'已配置' if config.API_KEY else '缺失'}")
    if not args.live:
        print("\n加 --live 实际调用一次，确认密钥与模型可用")
        return 0

    embedder = Embedder()
    texts = ["对方要求受害人下载刷单APP并转账。", "冒充公检法，要求资金转入安全账户。", "刷单返利类"]
    vectors = embedder.embed(texts)
    print(f"\n输入 {len(texts)} 条，返回 {len(vectors)} 条，维度 {embedder.dim}")
    print(f"首条前 5 维：{[round(v, 4) for v in vectors[0][:5]]}")

    single = embedder.embed_one(texts[0])
    # 同一条文本单独调用与批量调用的结果不必逐位相同：批量推理的浮点累加顺序不同，
    # 会有约 1e-4 量级的分量差。判断标准应是方向一致，而不是数值相等。
    dot = sum(a * b for a, b in zip(single, vectors[0]))
    norm_a = sum(a * a for a in single) ** 0.5
    norm_b = sum(b * b for b in vectors[0]) ** 0.5
    cosine = dot / (norm_a * norm_b)
    max_diff = max(abs(a - b) for a, b in zip(single, vectors[0]))
    print(f"embed_one 与批量余弦相似度：{cosine:.6f}   最大分量差 {max_diff:.2e}")
    ok = cosine > 0.9999
    print(f"判定：{'一致' if ok else '不一致'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
