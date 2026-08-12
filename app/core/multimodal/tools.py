import re

# data URI 前缀匹配：data:<type>/<subtype>;base64[,payload]
# 兼容大小写、复合子类型（如 image/svg+xml）、载荷缺失（无逗号）
_DATA_URI_RE = re.compile(r"data:([\w.+-]+)/([\w.+-]+);base64", re.IGNORECASE)

# 输入类型 → 默认后缀兜底（无法从内容解析时使用）
_EXTENSION_FALLBACK = {"image": "jpg", "video": "mp4", "audio": "mp3", "text": "txt"}


def get_extension(input_type: str, content: str) -> str:
    """
    提取文件后缀。

    :param input_type: 文件类型（image/video/audio/text，未知类型返回 bin）
    :param content: 文件编码内容（data URI 或纯 base64）
    :return: 后缀格式（小写；复合子类型如 image/svg+xml 取 svg）

    示例:
        get_extension("image", "data:image/png;base64,xxx")       -> "png"
        get_extension("image", "data:image/svg+xml;base64,xxx")   -> "svg"
        get_extension("image", "data:IMAGE/JPEG;base64,xxx")      -> "jpeg"
        get_extension("image", "raw_binary_without_prefix")       -> "jpg"
    """
    match = _DATA_URI_RE.match(content)
    if match:
        subtype = match.group(2).lower()
        # image/svg+xml -> svg；普通子类型（jpeg/mp4/png）原样返回
        return subtype.split("+")[0]
    return _EXTENSION_FALLBACK.get(input_type, "bin")


def extract_base64_payload(content: str) -> str:
    """
    从 data URI / 纯 base64 内容中提取 base64 载荷（去掉前缀与逗号）。

    :param content: data URI（如 data:image/png;base64,xxx）或纯 base64 字符串
    :return: 纯 base64 字符串

    示例:
        extract_base64_payload("data:image/png;base64,aGVsbG8=") -> "aGVsbG8="
        extract_base64_payload("DATA:IMAGE/PNG;base64,aGVsbG8=") -> "aGVsbG8="
        extract_base64_payload("aGVsbG8=")                       -> "aGVsbG8="
    """
    # 大小写不敏感识别 data URI（与 get_extension 的 _DATA_URI_RE 一致）
    if _DATA_URI_RE.match(content) and "," in content:
        return content.split(",", 1)[1]
    return content
