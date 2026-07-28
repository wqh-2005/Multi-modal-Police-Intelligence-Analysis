from fastapi import FastAPI
from app.api.multimodal_api import router as multimodal_router
app = FastAPI()


app.include_router(multimodal_router)