# RagEngine.py
import os
import hashlib
import re
import json
import time
from typing import List, Dict, Optional
from pathlib import Path
from dotenv import load_dotenv
from pprint import pprint
from langchain_chroma import Chroma
import asyncio
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from typing import List, Dict, Optional, Any
from langchain_core.embeddings import Embeddings
from openai import OpenAI

from app.models.SiliconFlowEmbedding import SiliconFlowEmbedding
from app.config import rag_cfg, com_cfg

class RagEngine:
    def __init__(self, persist_dir:str, collection_name:str):
        
        '''
        1. 初始化ChromaDB客户端
        2. 初始化Embedding模型
        3. 获取或创建collection
        '''

        self.collection_name = collection_name
        self.persist_dir = persist_dir


        self.embedding = SiliconFlowEmbedding(
            api_key=com_cfg.JUDGMENT_API_KEY,
            base_url=com_cfg.JUDGMENT_BASE_URL,
            model=rag_cfg.RAGENGING_MODEL,
        )

        self._vector_store = None
        self._initialized = False
        self.top_k = com_cfg.RAG_TOP_K

    def _ensure_chromas_successful(self):
        if not self._initialized:
            try:
                # 创建目录
                os.makedirs(self.persist_dir, exist_ok=True)
                
                # 初始化 Chroma
                self._vector_store = Chroma(
                    collection_name=self.collection_name,
                    embedding_function=self.embedding,
                    persist_directory=self.persist_dir
                )
                
                # 验证连接（轻量级心跳）
                self._vector_store._client.heartbeat()
                
                self._initialized = True
                
            except Exception as e:
                raise RuntimeError(f"ChromaDB 初始化失败: {str(e)}") from e

    @staticmethod
    def get_text_hash(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def format_to_json(self, file_path: str) -> Dict[str,Any]:
        with open(file_path,"r",encoding="utf-8") as f:
            data = json.load(f)
        
        file_name = os.path.basename(file_path)

        result = {
            "data_type":"unknown",
            "source":file_name,
            "records":[
            ]
        }


        if isinstance(data,dict) and "items" in data:
            result["data_type"] = "dialogue"

            for item in data["items"]:
                content = item.get("dialogue","")
                if not content or not content.strip():
                    continue

                record = {
                    "content": content,
                    "fraud_type":item.get("fraud_category_standard","未知")
                }

                result["records"].append(record)

        if isinstance(data,list):
            result["data_type"] = "case_summary"

            for item in data:
                content = item.get("案情描述","")
                if not content or not content.strip():
                    continue

                record = {
                    "content": content,
                    "fraud_type":item.get("案件类别","未知")
                }
                
                result["records"].append(record)

        return result

    
    def _get_existing_text_ids(self, ids: List[str]) -> set[str]:
        existing = set()
        for i in range(0,len(ids),500):
            batch = ids[i:i+500]
            result = self._vector_store._collection.get(ids = batch)
            if result and result.get("ids"):
                existing.update(result["ids"])
        return existing

    def load_from_json(self,file_path: str) -> int:
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        data = self.format_to_json(file_path)

        self._ensure_chromas_successful()

        records = data.get("records", [])
        if not records:
            print(f"文件 {file_path} 中没有有效的记录，跳过加载。")
            return 0

        docs = []
        seen = set()
        for record in records:
            content = record.get("content","")
            if not content.strip():
                continue
            text_hash = self.get_text_hash(content)
            if text_hash in seen:
                continue
            seen.add(text_hash)
            docs.append(Document(
                id = text_hash,
                page_content = content,
                metadata = {
                    "fraud_type": record.get("fraud_type", "未知"),
                    "source_file": data.get("source", "unknown"),
                    "data_type": data.get("data_type", "unknown")
                }
            ))

        print(f"正在加载{file_path}数据集")
        existing_ids = self._get_existing_text_ids([d.id for d in docs])
        print(f"已存在的文本哈希数量: {len(existing_ids)}")

        to_add = [d for d in docs if d.id not in existing_ids]
        if not to_add:
            print("没有新案例需要加载")
            return 0

        for i in range(0,len(to_add),100):
            batch = to_add[i:i+100]
            while True:
                try:
                    self._vector_store.add_documents(batch)
                    break
                except Exception as e:
                    msg = str(e)
                    if "429" in msg or "rate limit" in msg.lower():
                        print(f"触发限速(429)，等待 60 秒后重试...")
                        time.sleep(60)
                        continue
                    raise
            print(f"已加载 {min(i + 100, len(to_add))}/{len(to_add)} 条")
            time.sleep(2)   # 批次之间主动降速，避免频繁撞 429
           
        print(f"已成功加载{len(to_add)}条例子到知识库")
        return len(to_add)


      
    async def search(self, query: str) -> List[Dict]:
        self._ensure_chromas_successful()
        results = await asyncio.to_thread(
            self._vector_store.similarity_search_with_score,  
            query,                                           
            k=self.top_k,                                 
        )

        docs = []
        for doc, score in results:
            item = {
                "content": doc.page_content,
                "metadata": doc.metadata,
                "score": score,
            }
            docs.append(item)

        return docs

    def search_batch(self, queries: List[str]) -> List[List[Dict]]:
        all_docs = []
        for query in queries:
            all_docs.append(self.search(query))
        return all_docs


if __name__ == "__main__":
    pass
