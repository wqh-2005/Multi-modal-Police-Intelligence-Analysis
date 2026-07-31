from fastapi import UploadFile, File
from openai import OpenAI
from datetime import datetime
from pathlib import Path
from app.config.setting import settings
from app.models.multimodal_schema import BatchMultimodalResponse, BatchMultimodalRequest, OutputItem
from app.core.multimodal.tools import get_extension
import base64

# 定义一个对象用于和大模型对话
# param "api_key": 你的密钥
# param "base_url": 模型平台地址
client=OpenAI(
    api_key=settings.api_key,
    base_url=settings.base_url
)

async def transmit_file_content(file_content:str) -> str:
    """
    将图片Base64编码得到具体内容文本
    :param file_content: Base64编码
    :return: 具体内容文本
    """
    # b64_str, mime_type = await encode_upload_file(file)
    # full_image_url = f"data:{mime_type};base64,{b64_str}"
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
                            "url": file_content
                        }
                    }
                ]
            }
        ]
    )
    content = llm_res.choices[0].message.content
    return content

async def process_batch_task(payload: BatchMultimodalRequest) -> BatchMultimodalResponse:
    """
    模块1 函数，返回格式1.2
    :param payload: 传入的模型
    :return: 格式1.2
    """
    # 1. 拿到案件名称
    case_id = payload.case_id
    print(f"正在处理案件{case_id}")

    # 2-1. 获取日期字符串
    today_str = datetime.today().strftime('%Y-%m-%d')
    # 2-2. 创建目录结构
    target_dir = Path("data") / today_str / case_id
    target_dir.mkdir(parents=True, exist_ok=True)

    # 3-1. 创建结果容器
    all_responses = []
    # 这里进行根据文件格式分发给不同的处理函数
    for index, item in enumerate(payload.inputs):
        # 3-2 给文件编号并生成文件名
        seq_num = index + 1
        if item.type == "text":
            ext = "txt"
        else:
            ext = get_extension(item.type, item.content)

        file_name = f"{case_id}-{item.type}-{seq_num:02d}.{ext}"
        # 3-3 生成存储路径
        file_path = target_dir / file_name
        # 3-4 解码并永久化存储
        if item.type == "text":
            # 直接以文本模式 "w" 写入，并指定编码为 utf-8
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(item.content)
        else:
            # 图片/视频/音频：先解码，再以 "wb" 模式写入
            try:
                pure_base64 = item.content.split(",")[-1]
                decode_base64 = base64.b64decode(pure_base64)
                with open(file_path, "wb") as f:
                    f.write(decode_base64)
            except Exception as e:
                print(f"处理二进制文件失败: {e}")
                continue

        if item.type == "text":
            res = {
                "type": "text",
                "text": item.content,
                "status": "done",
                "deepfake_result": None
            }
        elif item.type == "image" or item.type == "audio":
            image_content = await transmit_file_content(item.content)
            res = {
                "type": "image",
                "text": image_content,
                "status": "done",
                "deepfake_result": False,
                "confidence": 0.98 # 先填默认值占位
            }
        elif item.type == "video":
            res = {
                "type": "audio",
                "text": "",
                "status": "",
                "deepfake_result": None
            }
            print("error: 不属于限制范围内的文件格式!")
            all_responses.append(OutputItem(**res))
    final_dict = {
        "case_id": case_id,
        "outputs": all_responses
    }
    return BatchMultimodalResponse(**final_dict)