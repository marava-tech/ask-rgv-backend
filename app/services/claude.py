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
    return response.content[0].text


async def sonnet_stream(messages: list[dict], system_blocks: list[dict]):
    client = get_client()
    async with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=system_blocks,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text
