from fastapi import APIRouter, Form, File, UploadFile, Depends, HTTPException
from app.core.multimodal.ocr import process_batch_task
from app.models.multimodal_schema import InputItem, BatchMultimodalRequest, BatchMultimodalResponse
router = APIRouter(
    prefix="/multimodal",
    tags=["多模态模块"],
)

# @router.post("/upload")
# async def ocr_api(file: UploadFile, multimodal_request: MultimodalRequest = Depends()) -> str:
#     return await ocr.distribute_file(multimodal_request, file)

# @router.post("/add")
# def add_api(a: int, b: int):
#     return ocr.add(a, b)

@router.post("/analyze")
async def analyze(payload: BatchMultimodalRequest)->BatchMultimodalResponse:
    """
    接受来自前端的模型，并返回格式1.2
    :param payload: 用户上传的，经前端打包的模型
    :return: 格式1.2
    """
    result = await process_batch_task(payload)
    return result


