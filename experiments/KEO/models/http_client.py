"""统一的 httpx 客户端：忽略系统代理。

为什么需要：Windows 的「系统代理」（注册表 WinINET）会被 ``urllib.request.getproxies()``
读到，而 httpx 默认 ``trust_env=True`` 会采用它。结果是本该直连的国内接口被绕到
代理上，表现是建索引跑到一半报
``httpx.ConnectError: EOF occurred in violation of protocol`` 后整段中断。

``api.siliconflow.cn`` 在国内，直连更快也更稳，所以 KEO 的模型调用一律
``trust_env=False``，不看环境变量和系统代理设置。
"""
from __future__ import annotations

import httpx

from experiments.KEO.configs import config


def make_http_client() -> httpx.Client:
    """构造一个不读系统代理的 httpx 客户端。"""
    return httpx.Client(trust_env=False, timeout=config.TIMEOUT)
