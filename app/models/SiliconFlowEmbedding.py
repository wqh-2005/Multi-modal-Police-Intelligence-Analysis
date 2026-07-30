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
from openai import OpenAI
import hashlib

class SiliconFlowEmbedding(Embeddings):
    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )
        self.model = model

    def embed_query(self, text: str) -> List[float]:
        """单条文本向量化（检索query使用）"""
        response = self.client.embeddings.create(
            model=self.model,
            input=text
        )
        return response.data[0].embedding

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量文档向量化（入库add_documents使用）"""
        response = self.client.embeddings.create(
            model=self.model,
            input=texts
        )
        # 按返回索引对齐向量
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]