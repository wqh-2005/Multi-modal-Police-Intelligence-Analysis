"""统一回答模型调用。

三种方法必须共用这一个入口：同一 ``model_id``、同一 ``temperature``、
同一 ``max_tokens``。否则 D 评出的差异无法归因到检索方式。

异常统一转成 ``AnswerError``，由 ``answerer.py`` 写进格式 1.4 的 ``error`` 字段，
保证「生成失败」在数据里可追溯，而不是丢一条记录。
"""
from __future__ import annotations

import argparse
import time

from openai import OpenAI

from experiments.KEO.configs import config
from experiments.KEO.models.http_client import make_http_client


class AnswerError(RuntimeError):
    """回答模型调用失败，携带错误类型以便写入格式 1.4。"""

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


class AnswerClient:
    """回答模型客户端；整个进程只实例化一次，保证三方法配置一致。"""

    def __init__(self, model_id: str | None = None,
                 temperature: float | None = None,
                 max_tokens: int | None = None,
                 api_key: str | None = None,
                 base_url: str | None = None):
        self.model_id = model_id or config.ANSWER_MODEL_ID
        self._temperature = config.ANSWER_TEMPERATURE if temperature is None else temperature
        self._max_tokens = max_tokens or config.ANSWER_MAX_TOKENS
        self._client = OpenAI(
            api_key=api_key or config.require_api_key(),
            base_url=base_url or config.BASE_URL,
            http_client=make_http_client(),   # 忽略系统代理，避免国内接口被绕
            max_retries=config.MAX_RETRIES,
        )

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def chat(self, prompt: str) -> tuple[str, int]:
        """返回 ``(回答原文, 耗时毫秒)``；失败抛 :class:`AnswerError`。"""
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model_id,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # noqa: BLE001 - 任何异常都要落成 error 字段
            message = str(exc)
            lowered = message.lower()
            error_type = "MODEL_TIMEOUT" if ("timeout" in lowered or "timed out" in lowered) \
                else "GENERATION_ERROR"
            raise AnswerError(error_type, message) from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        content = response.choices[0].message.content if response.choices else ""
        return (content or ""), elapsed_ms


def main() -> int:
    parser = argparse.ArgumentParser(description="回答模型连通性自测")
    parser.add_argument("--live", action="store_true", help="真正调用一次（会产生少量费用）")
    args = parser.parse_args()

    client = AnswerClient()
    print(f"模型        : {client.model_id}")
    print(f"temperature : {client.temperature}")
    print(f"max_tokens  : {client.max_tokens}")
    print(f"地址        : {config.BASE_URL}")
    print(f"密钥        : {'已配置' if config.API_KEY else '缺失'}")

    if not args.live:
        print("\n加 --live 实际调用一次，确认模型名可用且输出格式符合预期")
        return 0

    from experiments.KEO.core.text_rag import prompt as prompt_module

    text, elapsed = client.chat(prompt_module.build_prompt(
        "对方称刷单可以赚钱，并要求下载应用充值。", ""))
    print(f"\n耗时 {elapsed} ms")
    print("-" * 70)
    print(text)
    print("-" * 70)
    print(f"能解析类别行：{bool(prompt_module.CATEGORY_LINE.search(text))}")
    print(f"能解析证据行：{bool(prompt_module.CITE_LINE.search(text))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
