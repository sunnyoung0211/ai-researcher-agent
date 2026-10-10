"""价格表兜底（LiteLLM 没有收录的模型用这里）。单位：美元 / 百万 token。

LiteLLM 收录了的模型以它的价格为准。这里的 Claude 价格为 Anthropic 官方价（2026-10 核对）；
Haiku 5.5 为提示不超过 10 万 token 时的价格。其他模型使用前请核对官方价格页。
"""

from __future__ import annotations

PRICES: dict[str, tuple[float, float]] = {
    # model: (input, output)
    "anthropic/claude-haiku-5-5": (0.10, 0.50),
    "anthropic/claude-sonnet-5-5": (2.0, 10.0),
    "anthropic/claude-opus-5-5": (4.0, 20.0),
    "anthropic/claude-fable-5-1": (10.0, 50.0),
}
DEFAULT = (4.0, 20.0)  # 未收录的模型按较高的价格估算，宁可高估预算


def estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    model = model.removeprefix("openrouter/")  # OpenRouter 转发的模型按原厂价格估算
    key = model if "/" in model else f"anthropic/{model}"
    if key.startswith("anthropic/"):
        key = key.replace(".", "-")  # OpenRouter 写 claude-haiku-5.5，官方写 claude-haiku-5-5
    pin, pout = PRICES.get(key, DEFAULT)
    return round((input_tokens * pin + output_tokens * pout) / 1_000_000, 6)
