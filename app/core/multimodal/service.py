import asyncio
import base64
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
import uuid

import imageio_ffmpeg
from fastapi import UploadFile, File
from openai import OpenAI, AsyncOpenAI

from app.config.setting import settings
from app.models.multimodal_schema import (
    BatchMultimodalResponse,
    BatchMultimodalRequest,
    OutputItem,
)
from app.core.multimodal.tools import extract_base64_payload, get_extension
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

# 视频理解模型直接上传的大小上限（超过则回退音频转写，避免超长请求）
_VIDEO_MODEL_MAX_BYTES = 60 * 1024 * 1024  # 60 MB
# 超过该体积的视频先压缩再上传（常规视频码率/分辨率较高，直接上传又慢又容易被拒）
_VIDEO_COMPRESS_THRESHOLD = 8 * 1024 * 1024  # 8 MB
# 压缩后若仍过大，再降一档压缩（更小分辨率 + 时长截断）
_VIDEO_COMPRESS_HARD_CAP = 50 * 1024 * 1024  # 50 MB

_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _get_temp_video_path() -> str:
    """在模块 temp 目录创建临时 mp4 文件路径。"""
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
    os.makedirs(temp_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(suffix=".mp4", dir=temp_dir)
    os.close(fd)
    return temp_path


def _probe_video_duration(file_path: str) -> float | None:
    """用 moviepy 读取视频时长（秒）；失败返回 None。"""
    try:
        video = VideoFileClip(file_path)
        try:
            return float(video.duration)
        finally:
            video.close()
    except Exception as e:
        logger.warning("视频时长探测失败: %s", e)
        return None


def _compress_video_for_llm(src_path: str, dst_path: str) -> bool:
    """用 ffmpeg 将视频压缩为 H.264 mp4（模型友好格式）。

    两档策略：
      1. 1600 宽 + CRF 25（保留画面文字清晰度，体积通常可降 60%+，
         兼顾 OCR 效果与上传体积；此前 CRF28 会损失小字可读性）
      2. 若仍超过 _VIDEO_COMPRESS_HARD_CAP：854 宽 + CRF 32 + 截断前 180 秒

    压缩结果比原文件大或失败时返回 False（调用方回退原文件）。
    """
    def _run(scale_expr: str, crf: int, extra: list[str]) -> bool:
        cmd = [
            _FFMPEG, "-y", "-i", src_path,
            "-vf", f"scale='{scale_expr}':-2",
            "-r", "24",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart",
            *extra,
            dst_path,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=300)
            if proc.returncode != 0:
                logger.warning("视频压缩失败: %s", proc.stderr.decode(errors="ignore")[-500:])
                return False
            return os.path.exists(dst_path) and os.path.getsize(dst_path) > 0
        except Exception as e:
            logger.warning("视频压缩异常: %s", e)
            return False

    try:
        if _run("min(1600,iw)", 25, []):
            if os.path.getsize(dst_path) < os.path.getsize(src_path):
                return True
        # 第一档不达标（更大或超上限）：降档重压（截断前 180 秒）
        if _run("min(854,iw)", 32, ["-t", "180"]):
            return os.path.getsize(dst_path) < os.path.getsize(src_path)
    except Exception as e:
        logger.warning("视频压缩整体失败: %s", e)
    return False

# 常见视频扩展名 -> MIME
_VIDEO_MIME_MAP = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".flv": "video/x-flv",
    ".m4v": "video/x-m4v",
}


def _guess_video_mime(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    return _VIDEO_MIME_MAP.get(ext, "video/mp4")


async def _qwen_video_extract(file_path: str) -> str:
    """调用硅基流动 Qwen3-VL 视频理解模型，从视频中提取文字内容（画面文字 + 语音）。

    上传前先用 ffmpeg 压缩为 H.264 mp4（常规视频体积大，压缩后上传更快更稳）；
    压缩失败/结果更大时回退原文件。若最终文件仍过大，抛异常交由调用方回退。
    """
    if not settings.video_model:
        raise RuntimeError("未配置 VIDEO_MODEL，无法调用视频理解模型")

    # ---------- 压缩（仅大文件） ----------
    temp_video: str | None = None
    send_path = file_path
    try:
        if os.path.getsize(file_path) > _VIDEO_COMPRESS_THRESHOLD:
            temp_video = _get_temp_video_path()
            if _compress_video_for_llm(file_path, temp_video):
                send_path = temp_video
                logger.info(
                    "视频压缩完成: %dMB -> %dMB",
                    os.path.getsize(file_path) // (1024 * 1024),
                    os.path.getsize(temp_video) // (1024 * 1024),
                )
            else:
                logger.warning("视频压缩不可用，使用原文件")

        if os.path.getsize(send_path) > _VIDEO_MODEL_MAX_BYTES:
            raise RuntimeError(
                f"视频过大 ({os.path.getsize(send_path) // (1024 * 1024)}MB)，超过视频模型上限"
            )

        with open(send_path, "rb") as f:
            video_b64 = base64.b64encode(f.read()).decode()

        # 帧率必须为整数（硅基 API 不接受小数）；帧数按视频时长自适应：
        # ≤64s -> 64 帧（每秒 1 帧全覆盖），更长视频按档位扩帧以尽量覆盖全程
        duration = _probe_video_duration(send_path)
        fps = 1
        if duration and duration > 64:
            max_frames = 128 if duration <= 128 else 256
        else:
            max_frames = 64

        mime = _guess_video_mime(send_path)
        resp = await client.chat.completions.create(
            model=settings.video_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": f"data:{mime};base64,{video_b64}",
                                "max_frames": max_frames,
                                "fps": fps,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "请仔细观看这段视频，尽可能完整地提取其中的全部文字信息："
                                "1) 所有说话人说出的每一句话（逐字转写，不要省略、不要概括）；"
                                "2) 画面中出现的所有文字：字幕、标题、聊天记录、按钮/图标上的文字、"
                                "状态栏信息、任何屏幕文字，逐条列出，不要遗漏任何细节。"
                                "直接输出提取结果，不要任何解释、前言或总结。"
                            ),
                        },
                    ],
                }
            ],
            max_tokens=4096,  # 长视频文字量大，放宽输出上限避免截断
            timeout=300,  # 视频理解较慢，单独放宽超时
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return "未识别到文本"
        return text
    finally:
        if temp_video and os.path.exists(temp_video):
            try:
                os.remove(temp_video)
            except OSError:
                pass


async def _extract_audio_fallback(file_path: str) -> str:
    """回退方案：抽取视频音频轨道，走 Whisper 转写。"""
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
        return result if result.strip() else "未识别到文本"

    except Exception as e:
        logger.error(f"视频处理失败: {e}")
        return "识别失败"

    finally:
        if video is not None:
            video.close()
        if os.path.exists(temp_audio):
            os.remove(temp_audio)


async def transmit_vidio_content(file_path: str) -> str:
    """
    提取视频中的文字内容。

    主路径：硅基流动 Qwen3-VL 视频理解模型直接分析视频（画面文字 + 语音）；
    AI 换脸识别仍由百度 deepfake_engine 负责，与本函数互不影响。
    视频模型失败/未配置/文件过大时，回退为抽取音频轨道走 Whisper 转写。
    :param file_path: 视频文件路径
    :return: 视频文字内容
    """
    try:
        text = await _qwen_video_extract(file_path)
        if text.strip():
            return text
    except Exception as e:
        logger.warning(f"视频理解模型提取失败，回退音频转写: {e}")
    return await _extract_audio_fallback(file_path)


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
                pure_base64 = extract_base64_payload(item.content)
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
                pure_base64 = extract_base64_payload(item.content)
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
