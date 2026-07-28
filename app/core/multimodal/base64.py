import base64
from fastapi import UploadFile

async def encode_upload_file(file: UploadFile) -> tuple:
    """
    对文件进行编码
    :param file: 文件
    :return: 编码和文件类型名称
    """
    content = await file.read()
    b64_str = base64.b64encode(content).decode('utf-8')
    mine_type = file.content_type or 'application/octet-stream'
    return b64_str, mine_type