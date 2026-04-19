import anthropic
from core.config import settings

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def haiku_call(prompt: str, system: str = "") -> str:
    client = get_client()
    kwargs = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    response = await client.messages.create(**kwargs)
    # Bug #37: non-text blocks (tool_use, thinking) would raise AttributeError on .text
    text_blocks = [b for b in response.content if b.type == "text"]
    return text_blocks[0].text if text_blocks else ""


async def sonnet_stream(messages: list[dict], system_blocks: list[dict], usage_out: dict | None = None):
    """Streams tokens; optionally writes final usage to usage_out dict (Bug #12)."""
    client = get_client()
    async with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=system_blocks,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text
        # Bug #12: usage was discarded; persist to caller-supplied dict so tokens_used can be stored
        if usage_out is not None:
            final = await stream.get_final_message()
            usage_out["input_tokens"] = final.usage.input_tokens
            usage_out["output_tokens"] = final.usage.output_tokens
