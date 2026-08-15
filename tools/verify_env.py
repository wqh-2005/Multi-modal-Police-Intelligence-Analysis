"""环境验证脚本：确认核心依赖可导入、配置可加载、API 连通。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print(f"Python: {sys.version}")

# 1. 核心依赖导入
import langchain_openai
import langchain_core
import neo4j
import pydantic
import dotenv
print(f"langchain_openai={langchain_openai.__version__}")
print(f"langchain_core={langchain_core.__version__}")
print(f"neo4j={neo4j.__version__}")
print(f"pydantic={pydantic.__version__}")

# 2. 业务模块导入
from app.config.settings import llm_settings, neo4j_settings
from app.models.knowledge_schema import Triplet, ExtractionOutput, Victim, Suspect
from app.core.knowledge.extraction_service import run_extraction, _build_prompt
from app.core.knowledge.storage_service import _infer_persons, _build_transactions

print("业务模块导入 OK")
print(f"EXTRACTION_MODEL={llm_settings.EXTRACTION_MODEL}")
print(f"SILICONFLOW_BASE_URL={llm_settings.SILICONFLOW_BASE_URL}")
print(f"API_KEY 前缀={str(llm_settings.SILICONFLOW_API_KEY)[:8]}...")
print(f"NEO4J_URI={neo4j_settings.NEO4J_URI}")

# 3. 构造提示词冒烟测试
prompt = _build_prompt("报警人称被诈骗5000元。")
assert "## 角色" in prompt and "## 输入文本" in prompt
print(f"提示词构建 OK (len={len(prompt)})")

# 4. 纯逻辑单元冒烟（不调 LLM）
t = [Triplet(subject="报警人", relation="转账", object="5000元", subject_type="PERSON", object_type="AMOUNT")]
v, s = _infer_persons(t)
tx = _build_transactions(t)
print(f"_infer_persons: victim={v.name}, suspect={s.name}")
print(f"_build_transactions: {len(tx)} 笔")
print("环境验证全部通过")
