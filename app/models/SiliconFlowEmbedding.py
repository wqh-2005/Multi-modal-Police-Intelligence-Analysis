import os
import json
from typing import List, Dict, Optional
from pathlib import Path
from dotenv import load_dotenv
from pprint import pprint
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from typing import List, Dict, Optional, Any
from langchain_core.embeddings import Embeddings
from openai import BadRequestError, OpenAI
from openai import APIConnectionError
import httpx
import time
import hashlib

class SiliconFlowEmbedding(Embeddings):
    def __init__(self, api_key: str, base_url: str, model: str):
        # trust_env=False：忽略 Windows 系统代理。国内接口被代理绕行时会中途
        # 报 TLS 中断（EOF occurred in violation of protocol），长任务整段失败。
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.Client(trust_env=False, timeout=240.0),
            max_retries=3,
        )
        self.model = model
        self._max_chars = 1200

    def embed_query(self, text: str) -> List[float]:
        """单条文本向量化（检索query使用）"""
        return self._embed([text])[0]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量文档向量化（入库add_documents使用）"""
        return self._embed(list(texts))

    # ------------------------------------------------------------------
    def _embed(self, texts: List[str], attempt: int = 1) -> List[List[float]]:
        """带兜底的批量向量化。

        接口返回 400 时对半拆分重试；连接失败退避重试。单条仍失败时，
        先尝试把空文本替换为「未知」，再尝试截断，最后抛出可定位的错误。
        调用方（如 KEO 的 node_index）一次 400 就会整段中断，这里必须挡住。
        """
        if not texts:
            return []
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
                encoding_format="float",
            )
        except APIConnectionError as exc:
            if attempt >= 3:
                raise
            time.sleep(2 ** attempt)
            return self._embed(texts, attempt + 1)
        except BadRequestError as exc:
            if len(texts) > 1:
                middle = len(texts) // 2
                return self._embed(texts[:middle]) + self._embed(texts[middle:])
            text = texts[0]
            if not str(text).strip():
                return self._embed(["未知"])
            trimmed = str(text)[: self._max_chars]
            if len(trimmed) < len(str(text)):
                return self._embed([trimmed])
            raise RuntimeError(
                f"embedding 被拒且无法再拆分：长度 {len(str(text))} 字符，"
                f"内容前 80 字：{str(text)[:80]!r}。原始错误：{exc}"
            ) from exc

        # 按返回索引对齐向量
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]


