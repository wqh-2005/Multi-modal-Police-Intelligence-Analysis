from fastapi import UploadFile, File
from openai import OpenAI
from app.config.setting import settings
from app.models.multimodal_schema import MultimodalResponse, MultimodalRequest
from app.core.multimodal.base64 import encode_upload_file

# 定义一个对象用于和大模型对话
# param "api_key": 你的密钥
# param "base_url": 模型平台地址
client=OpenAI(
    api_key=settings.api_key,
    base_url=settings.base_url
)

async def image_from_url(multimodal_request: MultimodalRequest, file: UploadFile) -> str:
    """
    将图片文件识别成文字
    :param multimodal_request: 用户输入格式1.1
    :return:输出格式1.2
    """
    b64_str, mime_type = await encode_upload_file(file)
    full_image_url = f"data:{mime_type};base64,{b64_str}"
    llm_res = client.chat.completions.create(
        model=settings.model_name,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "识别图中文字，不要有多余输出"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": full_image_url
                        }
                    }
                ]
            }
        ]
    )
    content = llm_res.choices[0].message.content
    return content

async def distribute_file(multimodal_request: MultimodalRequest, file: UploadFile) -> str:
    """

    :param multimodal_request:
    :param file:
    :return:
    """
    if multimodal_request.input_type == "image":
        # 调用异步函数需要await
        return await image_from_url(multimodal_request, file)
    return ""