# RagEngine.py
import os
# import sys
# sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# from app.config.connfig import JUDGMENT_API_KEY, JUDGMENT_BASE_URL, RAGENGING_MODEL
import re
import json
from typing import List, Dict, Optional
from pathlib import Path
from dotenv import load_dotenv

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from typing import List, Dict, Optional, Any
from langchain_core.embeddings import Embeddings
from openai import OpenAI

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)

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

class RagEngine:
    def __init__(self, persist_dir:str, collection_name:str):
        
        '''
        1. 初始化ChromaDB客户端
        2. 初始化Embedding模型
        3. 获取或创建collection
        '''
        self.collection_name = collection_name
        self.persist_dir = persist_dir

        api_key = os.getenv("JUDGMENT_API_KEY")
        base_url = os.getenv("JUDGMENT_BASE_URL")
        model = "BAAI/bge-large-zh-v1.5" 


        self.embedding = SiliconFlowEmbedding(
            api_key=api_key,
            base_url=base_url,
            model=model,
        )


        # self._init_vector_store()

    # def _init_vector_store(self):
    #     """初始化或获取向量数据库"""
    #     # 确保目录存在
    #     os.makedirs(self.persist_dir, exist_ok=True)
    #     try:
    #         self.vector_store = Chroma(
    #             collection_name=self.collection_name,
    #             embedding_function=self.embedding,
    #             persist_directory=self.persist_dir
    #         )
    #     except:
    #         print("连接Chroma失败")

        self._vector_store = None
        self._initialized = False

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


    def format_to_json(self, file_path: str) -> Dict[str,Any]:
        with open(file_path,"r",encoding="utf-8") as f:
            data = json.load(f)
        
        file_name = os.path.basename(file_path)

        '''
        result = {
            "data_type":"unknown",
            "source":file_name,
            "records":[
                {
                    "id":
                    "content":"xxx",
                    "fraud_type":"xxx"
                }
            ]
        }
        '''

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


    def load_from_json(self,file_path: str) -> int:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        try:
            data = self.format_to_json(file_path)
        except Exception as e:
           print("转换为Json格式失败")


        '''
        result = {
            "data_type":"unknown",
            "source":file_name,
            "records":[
                {
                    "id":
                    "content":"xxx",
                    "fraud_type":"xxx"
                }
            ]
        }
        '''
        self._ensure_chromas_successful()

        documents = []
        records = data.get("records", [])
        for i, record in enumerate(records):
            content = record.get("content", "")
            if not content:
                continue
            doc = Document(
                page_content=content,
                metadata={
                    "id": i,
                    "fraud_type": record.get("fraud_type", "未知"),
                    "source_file": data.get("source", "unknown"),
                    "data_type": data.get("data_type", "unknown"),
                }
            )
            documents.append(doc)
                
        try:
            self._vector_store.add_documents(documents)
        except Exception as e:
            print("向量化调用API失败：", str(e))
            raise
        
        print(f"已成功加载 {len(documents)} 条案例到知识库")
        return len(documents)
        
    
    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        # self._ensure_chromas_successful()
        results = self._vector_store.similarity_search_with_score(query, k = top_k)

        print(results)
        print("---------------------------------------------")
        docs = []
        for doc, score in results:
            docs.append({
                "content": doc.page_content,
                "metadata": doc.metadata,
                "score": score,  # 距离分数，越小越相似
            })
        
        return docs

    # def search_batch(self, queries: List[str], top_k: int = 3) -> List[List[Dict]]:


if __name__ == "__main__":
    engine = RagEngine(
        "./text/processed",
        "fraud_cases"
    )

    engine.load_from_json(
        "./text/test_raw/测试诈骗案例.json"
    )

    print("成功")

    query = "我在网上兼职刷单，垫付了2万后提现不了"
    results = engine.search(query, top_k=2)
    
    print("\n🔍 检索结果:")
    for i, r in enumerate(results):
        print(f"{i+1}. {r['content'][:100]}... (相似度: {r['score']:.3f})")
