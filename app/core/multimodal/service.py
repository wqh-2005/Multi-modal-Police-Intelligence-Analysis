from asyncio.windows_events import NULL

from fastapi import UploadFile, File
from openai import OpenAI
from datetime import datetime
from pathlib import Path
from app.config.setting import settings
from app.models.multimodal_schema import BatchMultimodalResponse, BatchMultimodalRequest, OutputItem
from app.core.multimodal.tools import get_extension
from app.core.multimodal.ocr_engine import transmit_image_content
from app.core.multimodal.deepfake_engine import identify_ai_video
import base64

# 定义一个对象用于和大模型对话
# param "api_key": 你的密钥
# param "base_url": 模型平台地址
client=OpenAI(
    api_key=settings.api_key,
    base_url=settings.base_url
)

async def transmit_audio_content(file_path:str) -> str:
    """
    处理音频识别
    :param file_path: 文件路径
    :return: 文本识别结果
    """
    try:
        # OpenAI 的 Whisper 接口要求传入真实的文件句柄
        with open(file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",  # 专门的语音识别模型
                file=audio_file
            )
        return transcript.text  # 识别出的文字，及一个假定的高置信度
    except Exception as e:
        print(f"音频识别失败: {e}")
        return "音频识别失败"


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

        item.file_name = f"{case_id}-{item.type}-{seq_num:02d}.{ext}"
        # 3-3 生成存储路径
        item.file_path = target_dir / item.file_name
        # 3-4 解码并永久化存储
        if item.type == "text":
            # 直接以文本模式 "w" 写入，并指定编码为 utf-8
            with open(item.file_path, "w", encoding="utf-8") as f:
                f.write(item.content)
        else:
            # 图片/视频/音频：先解码，再以 "wb" 模式写入
            try:
                pure_base64 = item.content.split(",")[-1]
                decode_base64 = base64.b64decode(pure_base64)
                with open(item.file_path, "wb") as f:
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
        elif item.type == "audio":
            audio_content = await transmit_audio_content(str(item.file_path))
            res = {
                "type": "audio",
                "text": audio_content if audio_content is not None else "音频识别失败",
                "status": "done",
                "deepfake_result": None,
                "confidence": None # 先填默认值占位
            }
        elif item.type == "image":
            image_content, reg_confidence = await transmit_image_content(str(item.file_path))
            res = {
                "type": "image",
                "text": image_content,
                "status": "done",
                "deepfake_result": None,
                "confidence": reg_confidence,
            }
        elif item.type == "video":
            video_deepfake_result, video_confidence = identify_ai_video(item.file_path)
            res = {
                "type": "video",
                "text": None,
                "status": "done",
                "deepfake_result": video_deepfake_result,
                "confidence": video_confidence
            }
        else:
            res = {
                "status": "error",
            }
            print("error: 不属于限制范围内的文件格式!")
        all_responses.append(OutputItem(**res))
    final_dict = {
        "case_id": case_id,
        "outputs": all_responses
    }
    return BatchMultimodalResponse(**final_dict)
