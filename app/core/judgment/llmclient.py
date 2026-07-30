import os
import json
from typing import List, Dict, Optional
from pathlib import Path
from pprint import pprint
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from typing import List, Dict, Optional, Any
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
import traceback

from app.models.llmoutput import FraudJudgment
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
        }
        ...
    ]

    '''
    def judge(self, victim_info: str, similar_cases: list) -> str:
        # 核心方法：接收信息，调用 API，返回结果

        # print("====LLM接收参数====")
        # print("victim_info=", victim_info)
        # print("similar_cases=", similar_cases)

        case_text = "\n\n".join([
            f"【参考案例{i+1}】：\n案例描述：{c['content']}\n案例类型:{c['fraud_type']}" 
            for i, c in enumerate(similar_cases)
        ])
        
        input_data = {
            "victim_info": victim_info,
            "similar_cases": case_text,
        }

        struct_llm = self.LLM.with_structured_output(FraudJudgment)
        chain = self.chat_prompt | struct_llm

        try:
            result = chain.invoke(input_data)
            return result.model_dump()
        except Exception as e:

            # print(f"❌ 调用大模型失败: {str(e)}")
            # print("========完整异常堆栈========")
            # print(traceback.format_exc())
            # print("============================")

            return{
                "is_fraud": False,
                "fraud_type": "无法判断",
                "confidence": "低",
                "confidence_score": 0.2,
                "reason": f"调用失败: {str(e)}",
                "warning": "请人工复核"
            }

        '''
        输出结果：
        FraudJudgment(
            is_fraud="否",
            fraud_type="无法判断",
            confidence="低",
            confidence_score = ;
            reason=f"调用失败: {str(e)}",
            warning="请人工复核"
        )
        是一个类
        '''
    
if __name__ == "__main__":
    llm = LLMClient()
    # 受害人信息（字符串）
    victim_info = """
    受害人张先生，45岁，公司职员。2026年7月28日下午3点，接到一个自称是北京市公安局朝阳分局民警的电话（号码：010-XXXXXXX）。
    对方声称张先生的身份证被他人冒用，在武汉开立了一个银行账户，该账户涉及一起洗钱案件，涉案金额达200万元。
    对方要求张先生配合调查，需要将所有个人资金转入"安全账户"进行资金审查，否则将面临刑事责任。
    张先生信以为真，准备将自己银行卡中的38万元定期存款转出。在准备转账时，突然想起社区民警曾经宣传过反诈知识，便拨打了96110咨询。
    """

    # 相似案例（List，包含2个案例）
    similar_cases = [
        {
            "content": "2024年12月15日，王女士（52岁，退休教师）接到自称北京市公安局的电话，称其涉嫌参与一起洗钱案件，要求将全部存款转入指定的安全账户配合调查。王女士按照对方指示，分三次向对方提供的银行账户转账共计86万元。转账后对方失联，王女士发现被骗后报警。",
            "fraud_type": "冒充公检法及政府机关类"
        },
        {
            "content": "2025年3月8日，李女士（38岁，个体商户）接到自称上海市公安局的电话，称其身份证被人冒用注册公司，该公司涉嫌非法集资。对方要求李女士将所有存款转入安全账户进行资金验证。李女士先后向对方转账23万元，后来发现被骗。",
            "fraud_type": "冒充公检法及政府机关类"
        }
    ]

    result = llm.judge(victim_info,similar_cases)

    pprint(result,width=120)
    print("================================")
    print(type(result))
    