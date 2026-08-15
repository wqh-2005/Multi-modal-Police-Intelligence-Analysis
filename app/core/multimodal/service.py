import asyncio
import base64
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
import uuid

from fastapi import UploadFile, File
from openai import OpenAI, AsyncOpenAI

from app.config.setting import settings
from app.models.multimodal_schema import (
    BatchMultimodalResponse,
    BatchMultimodalRequest,
    OutputItem,
)
from app.core.multimodal.tools import get_extension
from app.core.multimodal.ocr_engine import transmit_image_content
from app.core.multimodal.deepfake_engine import identify_ai_video
from moviepy import VideoFileClip
from app.core.multimodal.tools import get_temp_audio_path

logger = logging.getLogger(__name__)

# 单个文件最大体积限制（解码后的字节数）
MAX_FILE_BYTES = 500 * 1024 * 1024  # 500 MB
# 文本内容最大字符数
MAX_TEXT_CHARS = 10 * 1024 * 1024  # 10 MB 等价字符

# 初始化 OpenAI 客户端
client = AsyncOpenAI(
    api_key=settings.api_key,
    base_url=settings.base_url,
    timeout=settings.timeout,
)


async def transmit_audio_content(file_path: str) -> str:
    """
    处理音频识别（Whisper），带重试
    """
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            with open(file_path, "rb") as audio_file:
                transcript = await client.audio.transcriptions.create(
                    model=settings.audio_model,
                    file=audio_file,
                )
            text = transcript.text.strip()
            if not text:
                return "未识别到文本"
            return text
        except Exception as e:
            logger.warning(f"音频识别失败 (第 {attempt}/{max_retries} 次): {e}")
            if attempt == max_retries:
                logger.error("音频识别多次失败，放弃")
                # 固定错误返回值
                return "识别失败"
    return "识别失败"

async def transmit_vidio_content(file_path: str) -> str:
    """
    提取视频中的文字
    如果无文字，则返回“从视频中未提取到内容”
    :param file_path: 视频文件路径
    :return: 视频文字内容
    """
    temp_audio = get_temp_audio_path(file_path)  # 生成临时音频路径
    video = None
    try:
        video = VideoFileClip(file_path)
        if video.audio is None:
            return "未识别到文本"  # 无音频轨道，直接返回固定值

        video.audio.write_audiofile(temp_audio)
        video.close()
        video = None

        # 调用音频识别，内部已处理空结果和异常
        result = await transmit_audio_content(temp_audio)
        # 再次保证非空（理论上 transmit_audio_content 已保证）
        # logger.info(result)
        return result if result.strip() else "未识别到文本"

    except Exception as e:
        logger.error(f"视频处理失败: {e}")
        return "识别失败"

    finally:
        if video is not None:
            video.close()
        if os.path.exists(temp_audio):
            os.remove(temp_audio)


async def process_batch_task(payload: BatchMultimodalRequest) -> BatchMultimodalResponse:

    # print(payload.inputs)
    """
    批量处理多模态输入，返回包含总耗时的响应
    """
    # 记录整个批处理的起始时间（秒）
    global_start_time = time.time()

    # 取16位随机
    short_suffix = uuid.uuid4().hex[:16]

    case_id = payload.case_id + '_' + short_suffix

    # ------------------ 空校验 ------------------
    if not case_id or not case_id.strip():
        error_item = OutputItem(
            type="text",
            text="案件ID不能为空，请提供有效的案件ID。",
            status="error",
            deepfake_result=None,
            confidence=None,
        )
        return BatchMultimodalResponse(
            case_id=case_id or "",  # 若为空则返回空字符串
            processing_time_ms=0,
            outputs=[error_item],
        )

    # ------------------ case_id 校验 ------------------
    # 允许的字符：大小写字母、中文、数字、- : ( ) [ ] { } _ .
    pattern = re.compile(r'^[a-zA-Z0-9一-龥\-:()\[\]{}\_.]+$')
    # 额外防护：拒绝纯 "." 或 ".." 等路径遍历 payload
    if not pattern.match(case_id) or case_id.strip() in (".", ".."):
        # 校验失败，立即返回错误响应
        error_item = OutputItem(
            type="text",
            text="案件ID包含非法字符，请仅使用字母、数字、中文、- : () [] {} _ .",
            status="error",
            deepfake_result=None,
            confidence=None,
        )
        return BatchMultimodalResponse(
            case_id=case_id,
            processing_time_ms=0,
            outputs=[error_item],
        )

    logger.info(f"正在处理案件: {case_id}")

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
            # ---------- 体积校验 ----------
            if item.type == "text":
                if len(item.content) > MAX_TEXT_CHARS:
                    raise ValueError(f"文本内容过大 ({len(item.content)} 字符)，上限 {MAX_TEXT_CHARS}")
            else:
                # 二进制文件：粗略估算 base64 解码后的体积（base64 膨胀率约 4/3）
                pure_base64 = item.content.split(",")[-1]
                estimated_bytes = len(pure_base64) * 3 // 4
                if estimated_bytes > MAX_FILE_BYTES:
                    raise ValueError(
                        f"文件过大 (约 {estimated_bytes // (1024*1024)} MB)，上限 {MAX_FILE_BYTES // (1024*1024)} MB"
                    )

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
                # 二次校验：实际解码后的体积
                if len(decode_base64) > MAX_FILE_BYTES:
                    raise ValueError(
                        f"解码后文件过大 ({len(decode_base64) // (1024*1024)} MB)，上限 {MAX_FILE_BYTES // (1024*1024)} MB"
                    )
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
                # 使用 asyncio.to_thread 避免同步 requests 调用阻塞事件循环
                deepfake_result, confidence = await asyncio.to_thread(
                    identify_ai_video, str(item.file_path)
                )
                video_text = await transmit_vidio_content(str(item.file_path))
                res_dict = {
                    "type": "video",
                    "text": video_text,
                    "status": "done",
                    "deepfake_result": deepfake_result,
                    "confidence": confidence,
                }
                print('======')
                print(res_dict)
            else:
                # 理论不会执行，因为 type 已被 Literal 限制
                res_dict = {
                    "type": item.type,
                    "text": "不支持的文件类型",
                    "status": "error",
                    "deepfake_result": None,
                    "confidence": None,
                }
                logger.warning(f"遇到了未定义的文件类型: {item.type}")

            output_item = OutputItem(**res_dict)

        except Exception as e:
            # 捕获任何异常，构造错误项，继续处理下一个输入
            logger.error(f"处理第 {index+1} 个文件时发生异常: {e}")
            # 不在响应中暴露内部路径或详细错误堆栈
            error_output.text = "处理失败，请检查文件格式或联系管理员"
            output_item = error_output

        all_responses.append(output_item)

    # ---------- 计算整个批处理的总耗时（毫秒） ----------
    total_ms = int((time.time() - global_start_time) * 1000)

    logger.info(f"案件 {case_id} 处理完成，共 {len(all_responses)} 项，耗时 {total_ms} ms")

    return BatchMultimodalResponse(
        case_id=case_id,
        processing_time_ms=total_ms,
        outputs=all_responses,
    )
