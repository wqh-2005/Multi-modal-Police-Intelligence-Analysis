from fastapi import APIRouter, Form, File, UploadFile, Depends, HTTPException
import app.core.multimodal.ocr as ocr
from app.models.multimodal_schema import MultimodalRequest
router = APIRouter(
    prefix="/multimodal",
    tags=["多模态模块"],
)

@router.post("/upload")
async def ocr_api(file: UploadFile, multimodal_request: MultimodalRequest = Depends()) -> str:
    return await ocr.distribute_file(multimodal_request, file)

# @router.post("/add")
# def add_api(a: int, b: int):
#     return ocr.add(a, b)

