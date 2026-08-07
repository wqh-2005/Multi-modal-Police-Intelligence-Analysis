import base64
import re
import time
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile, File
from openai import OpenAI

from app.config.setting import settings
from app.models.multimodal_schema import (
    BatchMultimodalResponse,
    BatchMultimodalRequest,
    OutputItem,
)
from app.core.multimodal.tools import get_extension
from app.core.multimodal.ocr_engine import transmit_image_content
from app.core.multimodal.deepfake_engine import identify_ai_video

# 初始化 OpenAI 客户端
client = OpenAI(
    api_key=settings.api_key,
    base_url=settings.base_url,
)


async def transmit_audio_content(file_path: str) -> str:
    """
    处理音频识别（Whisper）
    """
    try:
        with open(file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )
        return transcript.text
    except Exception as e:
        print(f"音频识别失败: {e}")
        return "音频识别失败"


async def process_batch_task(payload: BatchMultimodalRequest) -> BatchMultimodalResponse:
    """
    批量处理多模态输入，返回包含总耗时的响应
    """
    # 记录整个批处理的起始时间（秒）
    global_start_time = time.time()

    case_id = payload.case_id

    # ------------------ case_id 校验 ------------------
    # 允许的字符：大小写字母、中文、数字、- : ( ) [ ] { } _ .
    pattern = re.compile(r'^[a-zA-Z0-9\u4e00-\u9fa5\-:()\[\]{}\_.]+$')
    if not pattern.match(case_id):
        # 校验失败，立即返回错误响应
        error_item = OutputItem(
            type="text",
            text="案件ID包含非法字符，请仅使用字母、数字、- : () [] {} _ .",
            status="error",
            deepfake_result=None,
            confidence=None,
        )
        return BatchMultimodalResponse(
            case_id=case_id,
            processing_time_ms=0,   # 耗时极短，填0
            outputs=[error_item],
        )

    print(f"正在处理案件: {case_id}")

    # 创建存储目录
    today_str = datetime.today().strftime("%Y-%m-%d")
    target_dir = Path("data") / today_str / case_id
    target_dir.mkdir(parents=True, exist_ok=True)

    all_responses = []

    # 遍历每个输入项
    for index, item in enumerate(payload.inputs):
        seq_num = index + 1

        # 预先准备一个错误项（如果后续异常，将使用它）
        error_output = OutputItem(
            type=item.type,
            text="处理失败",
            status="error",
            deepfake_result=None,
            confidence=None,
        )

        try:
            # ---------- 生成文件名并保存文件 ----------
            if item.type == "text":
                ext = "txt"
            else:
                ext = get_extension(item.type, item.content)

            item.file_name = f"{case_id}-{item.type}-{seq_num:02d}.{ext}"
            item.file_path = target_dir / item.file_name

            if item.type == "text":
                with open(item.file_path, "w", encoding="utf-8") as f:
                    f.write(item.content)
            else:
                # 二进制文件：解码 base64
                pure_base64 = item.content.split(",")[-1]
                decode_base64 = base64.b64decode(pure_base64)
                with open(item.file_path, "wb") as f:
                    f.write(decode_base64)

            # ---------- 根据类型调用相应的识别引擎 ----------
            if item.type == "text":
                res_dict = {
                    "type": "text",
                    "text": item.content,
                    "status": "done",
                    "deepfake_result": None,
                    "confidence": None,
                }
            elif item.type == "audio":
                audio_text = await transmit_audio_content(str(item.file_path))
                res_dict = {
                    "type": "audio",
                    "text": audio_text if audio_text is not None else "音频识别失败",
                    "status": "done",
                    "deepfake_result": None,
                    "confidence": None,
                }
            elif item.type == "image":
                image_text, confidence = await transmit_image_content(str(item.file_path))
                res_dict = {
                    "type": "image",
                    "text": image_text,
                    "status": "done",
                    "deepfake_result": None,
                    "confidence": confidence,
                }
            elif item.type == "video":
                deepfake_result, confidence = identify_ai_video(item.file_path)
                res_dict = {
                    "type": "video",
                    "text": None,
                    "status": "done",
                    "deepfake_result": deepfake_result,
                    "confidence": confidence,
                }
            else:
                # 理论不会执行，因为 type 已被 Literal 限制
                res_dict = {
                    "type": item.type,
                    "text": "不支持的文件类型",
                    "status": "error",
                    "deepfake_result": None,
                    "confidence": None,
                }
                print("警告: 遇到了未定义的文件类型")

            output_item = OutputItem(**res_dict)

        except Exception as e:
            # 捕获任何异常，构造错误项，继续处理下一个输入
            print(f"处理第 {index+1} 个文件时发生异常: {e}")
            error_output.text = f"处理异常: {str(e)}"
            output_item = error_output

        all_responses.append(output_item)

    # ---------- 计算整个批处理的总耗时（毫秒） ----------
    total_ms = int((time.time() - global_start_time) * 1000)

    return BatchMultimodalResponse(
        case_id=case_id,
        processing_time_ms=total_ms,
        outputs=all_responses,
    )