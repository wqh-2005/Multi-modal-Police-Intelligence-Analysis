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
            "score": 0.0
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
            f"【参考案例{i+1}】：\n案例描述：{c['content']}\n案例类型:{c['fraud_type']}\n相似度: {c['score']:.3f}" 
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
    
    # ============================================================
    # 非诈骗场景：正常银行客服咨询
    # ============================================================
    victim_info = """
    李先生，32岁，个体商户。2026年7月30日上午10点，接到工商银行官方客服电话（95588），
    通知其名下信用卡有一笔境外消费存在异常，需要确认是否为本人操作。
    李先生登录工商银行手机APP查看，确实有一笔200美元的境外消费记录。
    客服指导李先生通过APP内渠道进行争议交易申报，并建议暂时冻结该卡。
    整个沟通过程中，客服没有要求李先生提供银行卡密码、验证码，
    没有要求转账到任何账户，也没有要求下载任何第三方软件。
    李先生通过官方APP完成操作后，问题得到妥善处理。
    """

    # 非诈骗场景的相似案例（正常业务）
    similar_cases = [
        {
            "content": "2025年1月10日，王先生收到银行官方短信提醒，称其信用卡在境外有异常消费，建议登录手机银行核实。王先生通过官方APP确认后，通过APP内渠道提交了盗刷申诉，银行随后为其处理了退款。整个流程均为银行官方渠道，无任何资金损失。",
            "fraud_type": "其它类"
        },
        {
            "content": "2025年4月5日，赵女士接到某平台官方客服电话，告知其账户存在异常登录，建议修改密码并开启双重验证。赵女士自行登录平台官网修改密码，未向任何人提供验证码，账户安全得到保障。",
            "fraud_type": "其它类"
        }
    ]

    result = llm.judge(victim_info, similar_cases)

    print("\n" + "=" * 60)
    print("📊 研判结果（非诈骗场景）")
    print("=" * 60)
    pprint(result, width=120)
    print("================================")
    print(type(result))