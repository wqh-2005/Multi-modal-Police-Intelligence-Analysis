from typing import Tuple, Optional
import os
import subprocess
import tempfile
import time
import logging

import imageio_ffmpeg
from app.config.setting import settings
import requests
import base64

logger = logging.getLogger(__name__)

API_KEY = settings.baidu_api_key
SECRET_KEY = settings.baidu_secret_key

# ffmpeg 可执行文件（用于把视频转码为百度活体检测要求的 H.264 MP4 + AAC）
_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# 缓存的 access_token
_cached_token: Optional[str] = None
_token_expires_at: float = 0.0
TOKEN_BUFFER_SECONDS = 300  # 提前 5 分钟刷新，避免临界过期


def get_access_token():
    """
    获取百度云 API 访问令牌（带缓存）
    :return: access_token 字符串，失败返回 None
    """
    global _cached_token, _token_expires_at

    # 如果缓存有效，直接返回
    if _cached_token and time.time() < _token_expires_at:
        return _cached_token

    url = (
        f"https://aip.baidubce.com/oauth/2.0/token"
        f"?grant_type=client_credentials"
        f"&client_id={API_KEY}"
        f"&client_secret={SECRET_KEY}"
    )
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    try:
        response = requests.post(url, headers=headers, timeout=settings.timeout)
        if response.status_code == 200:
            data = response.json()
            _cached_token = data.get("access_token")
            # 百度 token 有效期通常为 30 天，按返回值中的 expires_in 计算，默认 1 小时
            expires_in = data.get("expires_in", 3600)
            _token_expires_at = time.time() + expires_in - TOKEN_BUFFER_SECONDS
            logger.info("百度 API access_token 刷新成功")
            return _cached_token
        else:
            logger.error(f"获取百度 access_token 失败，HTTP {response.status_code}: {response.text}")
            return _cached_token  # 如果有旧 token 则返回旧的作为兜底
    except Exception as e:
        logger.error(f"获取百度 access_token 网络异常: {e}")
        if _cached_token:
            logger.warning("网络异常，使用已缓存的 access_token 作为兜底")
            return _cached_token
        return None


def _safe_remove(path: Optional[str]) -> None:
    """安全删除临时文件（忽略不存在等异常）。"""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _download_to_local(url: str, dst_path: str) -> bool:
    """下载公网视频到本地临时文件。"""
    try:
        with requests.get(url, timeout=120, stream=True) as r:
            r.raise_for_status()
            with open(dst_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return os.path.getsize(dst_path) > 0
    except Exception as e:
        logger.error(f"视频下载失败 [{url}]: {e}")
        return False


def _transcode_for_baidu(src_path: str, dst_path: str) -> bool:
    """转码为百度活体检测要求的格式：H.264 MP4 + AAC，降分辨率以控制体积。"""
    cmd = [
        _FFMPEG, "-y", "-i", src_path,
        "-vf", "scale='min(720,iw)':-2",
        "-r", "24",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "64k",
        "-movflags", "+faststart",
        dst_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=180)
        if proc.returncode != 0:
            logger.warning("视频转码失败: %s", proc.stderr.decode(errors="ignore")[-500:])
            return False
        return os.path.exists(dst_path) and os.path.getsize(dst_path) > 0
    except Exception as e:
        logger.warning("视频转码异常: %s", e)
        return False


def identify_ai_video(video_path):
    """
    识别是否为 AI 换脸

    :param video_path: 视频本地路径或公网 URL
    :return:
    - is_ai: True 表示疑似 AI 换脸，False 表示正常
    - confidence: 百度返回的 maxspoofing 置信度分数 (0~1)
    """
    token = get_access_token()
    if not token:
        logger.error("无法获取百度 access_token，跳过深度伪造检测")
        return False, 0.0

    url = f"https://aip.baidubce.com/rest/2.0/face/v1/faceliveness/verify?access_token={token}"
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    # 百度 H5 活体检测接口只支持 video_base64，不支持 video_url（传 URL 会报
    # 216508 not found video info）。因此统一把视频落到本地并转码为 H.264 MP4 + AAC，
    # 再 base64 上传，避免 URL 不可达或编码不兼容（屏幕录制常为 VP9/HEVC）。
    tmp_download = None
    tmp_conv = None
    src = video_path
    try:
        if video_path.startswith(('http://', 'https://')):
            fd, tmp_download = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)
            if not _download_to_local(video_path, tmp_download):
                return False, 0.0
            src = tmp_download

        fd, tmp_conv = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        send_path = src
        if _transcode_for_baidu(src, tmp_conv):
            send_path = tmp_conv
        else:
            logger.warning("视频转码失败，回退使用原始文件")

        with open(send_path, "rb") as f:
            video_b64 = base64.b64encode(f.read()).decode("utf-8")
        logger.info("深度伪造检测上传视频: %d 字节", os.path.getsize(send_path))
    except Exception as e:
        logger.error(f"视频读取/编码失败 [{video_path}]: {e}")
        return False, 0.0
    finally:
        _safe_remove(tmp_download)
        _safe_remove(tmp_conv)

    payload = {
        "video_base64": video_b64,
        "face_field": "spoofing"
    }

    # 发送请求（带超时、重试和指数退避）
    max_retries = 3
    res_data = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=120)
            res_data = response.json()
            break
        except requests.exceptions.Timeout:
            logger.warning(f"百度 API 超时 (第 {attempt}/{max_retries} 次)")
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"百度 API 连接异常 (第 {attempt}/{max_retries} 次): {e}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"百度 API 请求异常 (第 {attempt}/{max_retries} 次): {e}")

        if attempt == max_retries:
            logger.error("百度 API 多次请求失败，放弃本次检测")
            return False, 0.0

        backoff = 2 ** attempt
        logger.info(f"等待 {backoff}s 后重试...")
        time.sleep(backoff)

    # 解析结果
    if res_data is None:
        logger.error("百度 API 返回空响应")
        return False, 0.0

    if res_data.get("error_code") == 0:
        result = res_data.get("result", {})
        confidence = result.get("maxspoofing", 0.0)
        is_ai = confidence > 0.00048
        logger.info(f"深度伪造检测完成: is_ai={is_ai}, confidence={confidence:.6f}")
        return is_ai, confidence
    else:
        error_msg = res_data.get("error_msg", "未知错误")
        error_code = res_data.get("error_code", -1)
        logger.error(f"百度 API 返回错误 [code={error_code}]: {error_msg}")
        return False, 0.0


if __name__ == '__main__':
    pass
