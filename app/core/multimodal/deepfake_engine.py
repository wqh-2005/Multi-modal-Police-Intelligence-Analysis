from typing import Tuple

from app.config.setting import settings
import requests
import json
import base64

API_KEY = settings.baidu_api_key
SECRET_KEY = settings.baidu_secret_key

def get_access_token():
    """
    获取百度云API访问令牌
    :return:
    """
    url = f"https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials&client_id={API_KEY}&client_secret={SECRET_KEY}"

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    response = requests.request("POST", url, headers=headers)
    if response.status_code == 200:
        return response.json().get("access_token")
    return None


def identify_ai_video(video_path):
    """
    识别是否为 AI 换脸
    :param video_path: 视频路径
    :return:
    - is_ai: True 表示疑似 AI 换脸，False 表示正常
    - confidence: 百度返回的 maxspoofing 置信度分数 (0~1)
    """
    token = get_access_token()
    if not token:
        return False, 0.0

    # 1. 读取视频文件并转为 Base64
    try:
        with open(video_path, "rb") as f:
            video_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        print(f"文件读取失败: {e}")
        return False, 0.0

    # 2. 准备请求接口 (视频活体检测 - 合成图识别模式)
    url = f"https://aip.baidubce.com/rest/2.0/face/v1/faceliveness/verify?access_token={token}"

    # 核心参数说明：
    # face_field=spoofing: 激活合成图（换脸）检测功能
    payload = {
        "video_base64": video_data,
        "face_field": "spoofing"
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    # 3. 发送请求
    try:
        response = requests.post(url, data=payload, headers=headers)
        res_data = response.json()
    except Exception as e:
        print(f"请求接口异常: {e}")
        return False, 0.0

    # 4. 解析结果
    if res_data.get("error_code") == 0:
        result = res_data.get("result", {})
        # maxspoofing: 合成图检测得分，数值越高风险越大
        confidence = result.get("maxspoofing", 0.0)

        # 根据百度官方推荐阈值：0.00048
        # 低于此值通常视为真人，高于此值视为合成/换脸攻击
        is_ai = confidence > 0.00048

        return is_ai, confidence
    else:
        # 如果调用失败（如视频里没检测到人脸、格式错误等），返回 False 和 0
        error_msg = res_data.get("error_msg")
        print(f"识别接口返回错误: {error_msg}")
        return False, 0.0


if __name__ == '__main__':
    pass