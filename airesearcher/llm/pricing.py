"""价格表兜底（LiteLLM 不覆盖时用）。单位：美元 / 百万 token。第 1 周小样本比较后更新。"""

from __future__ import annotations

PRICES: dict[str, tuple[float, float]] = {
    # model: (input, output)
    "anthropic/claude-haiku-5-5": (1.0, 5.0),
    "anthropic/claude-sonnet-5-5": (3.0, 15.0),
    "anthropic/claude-opus-5-5": (5.0, 25.0),
    "openai/gpt-5-mini": (0.25, 2.0),
}
DEFAULT = (3.0, 15.0)


def estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, DEFAULT)
    return round((input_tokens * pin + output_tokens * pout) / 1_000_000, 6)
