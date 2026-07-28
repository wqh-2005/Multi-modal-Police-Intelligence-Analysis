# RagEngine.py
import os
import re
import json
from typing import List, Dict, Optional
from pathlib import Path
from dotenv import load_dotenv

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from langchain_community.document_loaders import TextLoader

from app.config import connfig 



class RagEngine:
    def __init__(slef, persist_dir:str, collection_name:str):
        
        '''
        1. 初始化ChromaDB客户端
        2. 初始化Embedding模型
        3. 获取或创建collection
        '''
        self.collection_name = collection_name
        self.persist_dir = persist_dir

        self.embedding = OpenAIEmbeddings(
            api_key = connfig.JUDGMENT_API_KEY,
            base_url = connfig.JUDGMENT_BASE_URL,
            model = connfig.RAGENGING_MDOEL
        )

        self._vector_store = null
        self._initialized = False

    def _ensure_chromas_successful(self):
        if not self._initialized:
            try:
                # 创建目录
                os.makedirs(self.persist_dir, exist_ok=True)
                
                # 初始化 Chroma
                self._vector_store = Chroma(
                    collection_name=self.collection_name,
                    embedding_function=self.embeddings,
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
                {

                }
            ]
        }


        if isinstance(data,dict) and "items" in data:
            result["data_type"] = "dialogue"

            for itme in data["items"]:
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
                
        self.vector_store.add_documents(documents)
        self.vector_store.persist()
        
        print(f"已成功加载 {len(documents)} 条案例到知识库")
        return len(documents)
        
    
    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        self._ensure_chromas_successful()
        results = self._vector_store.similarity_search_with_score(query, k = teop_k)

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
    engine = RAGEngine(
        "./data/processd",
        "fraud_cases"
    )

    engine.load_from_json(
        "./text/test_raw/测试诈骗案例.json"
    )

    query = "我在网上兼职刷单，垫付了2万后提现不了"
    results = engine.search(query, top_k=2)
    
    print("\n🔍 检索结果:")
    for i, r in enumerate(results):
        print(f"{i+1}. {r['content'][:100]}... (相似度: {r['score']:.3f})")
