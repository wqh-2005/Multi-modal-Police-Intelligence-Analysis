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
        你是专业反诈研判专家，严格依据受害人描述与历史诈骗案例完成风险研判。

        一、诈骗类型标准（仅允许使用以下11类，无匹配则填「其它类」）
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

        二、研判规则
        1. 判定依据：优先参考提供的相似历史案例，案情特征重合度越高，置信度越高；
        2. is_fraud 规则：
        - 存在典型诈骗话术、诱导转账/泄露银行卡、验证码、屏幕共享 → true
        - 正常官方业务、线下正规办理、无任何诈骗诱导行为 → false
        3. confidence 三档划分：
        - 高：特征高度匹配诈骗案例，证据充分，几乎无争议
        - 中：存在可疑风险点，但信息不全、部分特征不典型
        - 低：线索不足、信息矛盾，无法明确判定是否为诈骗
        4. confidence_score：0~1之间浮点数，与confidence档位对应：
        - 高：0.85 ~ 1.0
        - 中：0.5 ~ 0.84
        - 低：0 ~ 0.49
        5. reason 要求：必须结合受害人信息+相似案例特征说明判断逻辑，不能泛泛而谈；
        6. warning 区分场景：
        - 若is_fraud=true：给出紧急劝阻话术，明确禁止转账、不要提供验证码、拨打96110反诈专线；
        - 若is_fraud=false：给出常规反诈提醒，告知后续如何识别同类骗局。

        三、强制输出规范
        1. 仅输出纯JSON字符串，不能附带任何解释、思考过程、注释、markdown、换行说明；
        2. 字段类型严格遵守：
        - is_fraud：布尔值 true / false，不能使用“是/否”字符串；
        - fraud_type：字符串，只能从上面11类中选取；
        - confidence：字符串，仅允许「高/中/低」；
        - confidence_score：纯数字（float），不要加引号；
        - reason、warning：字符串；
        3. 禁止输出残缺JSON、多余逗号、转义错误文本。

        标准JSON模板
        {{
            "is_fraud": true,
            "fraud_type": "冒充公检法及政府机关类",
            "confidence": "高",
            "confidence_score": 0.96,
            "reason": "受害人接到自称公安人员来电，以涉案洗钱为由要求转入安全账户，与参考案例诈骗手法完全一致，属于典型冒充公检法诈骗特征",
            "warning": "立刻停止任何转账操作，公安机关不存在安全账户，切勿透露银行卡、验证码，立即拨打96110反诈专线核实求助"
        }}
    """

settings = Settings()