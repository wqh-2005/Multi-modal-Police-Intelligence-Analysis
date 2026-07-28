import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / "env")

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
RAGENGING_MDOEL = "BAAI/bge-large-zh-v1.5"


'''
实现json格式映射
'''

