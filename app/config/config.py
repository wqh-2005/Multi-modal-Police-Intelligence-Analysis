import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

class Settings:

    '''
    路径配置
    '''

    '''
    敏感信息配置
    '''
    JUDGMENT_API_KEY = os.getenv("JUDGMENT_API_KEY")
    JUDGMENT_BASE_URL = os.getenv("JUDGMENT_BASE_URL")

    '''
    模型配置
    '''
    RAGENGING_MODEL = "BAAI/bge-large-zh-v1.5"
    LLMMODEL = "Qwen/Qwen2.5-32B-Instruct"

    # ✅ 修复：用双括号转义 JSON 格式中的大括号
    SYSTEM_PROMPT = """
        你是一个专业的反电信诈骗研判专家。

        【任务】
        根据受害者信息和相似案例，判断受害者是否正在遭受电信诈骗。

        【诈骗类型分类标准】
        参考《公安部公布十大高发电信网络诈骗类型》：
        1. 刷单返利类
        2. 虚假网络投资理财类
        3. 虚假购物服务类
        4. 冒充电商物流客服类
        5. 虚假贷款类
        6. 虚假征信类
        7. 冒充领导熟人类
        8. 冒充公检法及政府机关类
        9. 网络婚恋、交友类
        10. 网络游戏产品虚假交易类
        11. 其它类

        【输出格式】
        必须返回合法的JSON，格式如下：
        {{
            "is_fraud": true/false,
            "fraud_type": "从11种中选择",
            "confidence": "高/中/低",
            "confidence_score": 0.95,
            "reason": "判断理由",
            "warning": "预警建议"
        }}

        【要求】
        1. 只输出JSON，不要有其他内容
        2. 如果确定是诈骗，confidence必须是"高"
        3. 如果无法确定，confidence为"低"
        4. warning要给出具体的防范建议
    """

settings = Settings()