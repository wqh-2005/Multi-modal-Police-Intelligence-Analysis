from fastapi import FastAPI
from app.api.multimodal_api import router as multimodal_router
from app.api.knowledge import router as knowledge_router

app = FastAPI()

# 注册多模态模块路由（第一模块）
app.include_router(multimodal_router)

# 注册知识抽取与知识图谱路由（模块二/三）
app.include_router(knowledge_router)
