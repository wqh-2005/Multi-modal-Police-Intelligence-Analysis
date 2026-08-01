# RagEngine.py
import os
import hashlib
import re
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
            raise

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
            content = record.get("content","")

            if not content.strip():
                continue

            text_hash = self.get_text_hash(content)
            exist = self._vector_store._collection.get(where={"text_hash":text_hash})
            if len(exist["ids"]) > 0:
                continue

            doc = Document(
                page_content=content,
                metadata={
                    "id": i,
                    "fraud_type": record.get("fraud_type", "未知"),
                    "source_file": data.get("source", "unknown"),
                    "data_type": data.get("data_type", "unknown"),
                    "text_hash":text_hash,
                }
            )
            documents.append(doc)
                
        try:
            if documents:
                self._vector_store.add_documents(documents)
        except Exception as e:
            print("向量化调用API失败：", str(e))
            raise
        
        print(f"已成功加载 {len(documents)} 条案例到知识库")
        return len(documents)
        

    def search(self, query: str) -> List[Dict]:
        self._ensure_chromas_successful() # 你之前注释了，建议打开，防止未初始化报错
        results = self._vector_store.similarity_search_with_score(query, k = self.top_k)

        # 1. 打印底层原始检索结果（格式化）
        # print("=====原始similarity_search_with_score返回结果=====")
        # pprint(results, width=140)
        # print("---------------------------------------------")
        
        docs = []
        for doc, score in results:
            item = {
                "content": doc.page_content,
                "metadata": doc.metadata,
                "score": score,
            }
            docs.append(item)

        # 2. 打印封装好的字典结果（最直观）
        print("=====封装后对外输出的字典列表=====")
        pprint(docs, width=140)
        print("--------------------------------------------------------------------------------------------")

        return docs

    def search_batch(self, queries: List[str]) -> List[List[Dict]]:
        all_docs = []
        for query in queries:
            all_docs.append(self.search(query))
        return all_docs


if __name__ == "__main__":
    engine = RagEngine(
        "./text/processed",
        "fraud_cases"
    )

    engine.load_from_json(
        "./text/test_raw/测试诈骗案例.json"
    )

    print("成功")

    query1 = "我在网上兼职刷单，垫付了2万后提现不了"
    query2 = "受害人接到自称抖音客服的电话，称其开通了直播会员服务，每月将扣费500元，要求下载屏幕共享软件进行取消操作，被骗转账2.3万元。"
    queries= []
    queries.append(query1)
    queries.append(query2)
    results = engine.search_batch(queries)
    
    print("\n🔍 检索结果:")
    # print(results)

    pprint(results,width = 120)
    print("---------------------------------------------------------")
