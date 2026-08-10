"""项目全局配置。

所有配置通过 os.getenv() 从 .env 文件中读取。
模块导入时自动调用 load_dotenv() 加载 .env。

分层结构:
    LLMSettings: 硅基流动 API + 模型参数
    Neo4jSettings: 图数据库连接
    ChromaDBSettings: 向量数据库
    AppSettings: 应用基础

使用示例:
    from app.config.settings import llm_settings
    key = llm_settings.SILICONFLOW_API_KEY
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录 = 当前文件向上三级（app/config → app → 项目根）
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 启动时立即加载 .env，将所有变量注入 os.environ
load_dotenv(str(PROJECT_ROOT / ".env"))


class LLMSettings:
    """LLM 配置，知识抽取与智能研判共用。

    Attributes:
        SILICONFLOW_API_KEY: 硅基流动 API 密钥（必填，无默认值）。
        SILICONFLOW_BASE_URL: 硅基流动 API 地址，兼容 OpenAI 协议。
        EXTRACTION_MODEL: 知识抽取使用的模型名称。
        EXTRACTION_TEMPERATURE: 知识抽取温度参数，0-1，越低越确定。
    """

    @property
    def SILICONFLOW_API_KEY(self):
        return os.getenv("SILICONFLOW_API_KEY")

    @property
    def SILICONFLOW_BASE_URL(self):
        return os.getenv("SILICONFLOW_BASE_URL")

    @property
    def EXTRACTION_MODEL(self):
        return os.getenv("EXTRACTION_MODEL")

    @property
    def EXTRACTION_TEMPERATURE(self):
        return float(os.getenv("EXTRACTION_TEMPERATURE"))


class Neo4jSettings:
    """Neo4j 图数据库配置。

    Attributes:
        NEO4J_URI: Bolt 连接地址。
        NEO4J_USER: 用户名。
        NEO4J_PASSWORD: 密码。
    """

    @property
    def NEO4J_URI(self):
        return os.getenv("NEO4J_URI")

    @property
    def NEO4J_USER(self):
        return os.getenv("NEO4J_USER")

    @property
    def NEO4J_PASSWORD(self):
        return os.getenv("NEO4J_PASSWORD")


class ChromaDBSettings:
    """ChromaDB 向量数据库配置。

    Attributes:
        CHROMA_PERSIST_DIR: 持久化目录。
        CHROMA_COLLECTION: 集合名称。
    """

    @property
    def CHROMA_PERSIST_DIR(self):
        return os.getenv("CHROMA_PERSIST_DIR")

    @property
    def CHROMA_COLLECTION(self):
        return os.getenv("CHROMA_COLLECTION")


class AppSettings:
    """应用基础配置。

    Attributes:
        APP_ENV: 运行环境，dev/prod。
        API_KEY: API 接口认证密钥。
    """

    @property
    def APP_ENV(self):
        return os.getenv("APP_ENV")

    @property
    def API_KEY(self):
        return os.getenv("API_KEY")


# 模块级单例，全局统一引用
llm_settings = LLMSettings()
neo4j_settings = Neo4jSettings()
chroma_settings = ChromaDBSettings()
app_settings = AppSettings()
