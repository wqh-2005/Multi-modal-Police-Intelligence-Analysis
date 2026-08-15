import os
import json
from typing import List, Dict, Optional
from pathlib import Path
from pprint import pprint
import asyncio
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from typing import List, Dict, Optional, Any
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
import traceback

from app.models.output.llmoutput import FraudJudgment
from app.config import llm_cfg, com_cfg, SYSTEM_PROMPT

class LLMClient:
    def __init__(self):

        # 初始化：读取 API Key，配置模型参数
        self.LLM = ChatOpenAI(
            api_key = com_cfg.JUDGMENT_API_KEY,
            base_url = com_cfg.JUDGMENT_BASE_URL,
            model = llm_cfg.LLMMODEL,
            temperature = 0,
            max_tokens = 1000
        )

        user_template = '''
                    【受害人信息】
                    {victim_info}

                    【相似历史案例参考】
                    {similar_cases}

                    请根据以上信息，对受害者是否遭受诈骗进行专业的研判。
        '''

        self.system_prompt = SystemMessagePromptTemplate.from_template(SYSTEM_PROMPT)
        self.user_prompt = HumanMessagePromptTemplate.from_template(user_template)
        self.chat_prompt = ChatPromptTemplate.from_messages([
            self.system_prompt,
            self.user_prompt,
        ])

    '''
    similar_cases = [
        {
            "content":"XXX"
            "fraud_type":"XXX"
            "score": 0.0
        }
        ...
    ]

    '''
    async def judge(self, victim_info: str, similar_cases: list) -> str:

        case_text = "\n\n".join([
            f"【参考案例{i+1}】：\n案例描述：{c['content']}\n案例类型:{c['fraud_type']}\n相似度: {c['score']:.3f}" 
            for i, c in enumerate(similar_cases)
        ])
        
        input_data = {
            "victim_info": victim_info,
            "similar_cases": case_text,
        }

        struct_llm = self.LLM.with_structured_output(FraudJudgment)
        chain = self.chat_prompt | struct_llm

        return  await chain.ainvoke(input_data)

        
    
if __name__ == "__main__":
    pass