import os
import re
import tempfile


def get_extension(input_type: str, content: str) -> str:
    """
    提取文件后缀
    :param input_type: 文件类型
    :param content: 文件编码内容
    :return: 后缀格式
    """
    # 如果内容里有 data:image/png;base64, 这种，提取中间的 png
    match = re.match(r"data:(\w+)/(\w+);base64,", content)
    if match:
        return match.group(2)
    # 兜底方案：根据输入类型给默认后缀
    mapping = {"image": "jpg", "video": "mp4", "audio": "mp3", "text": "txt"}
    return mapping.get(input_type, "bin")


def get_temp_audio_path(file_path: str) -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(script_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(suffix=".mp3", dir=temp_dir)
    os.close(fd)
    return temp_path