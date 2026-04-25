import logging

from services.prompt import SYSTEM_PERSONA

_log = logging.getLogger(__name__)


async def warm_prompt_cache() -> None:
    """
    Fire a 1-token Sonnet call per language at startup to seed Anthropic's 5-min
    ephemeral cache on the static persona block. The first real user turn then gets
    a cache hit, saving ~300 ms on TTFT.

    Called once in lifespan startup — no scheduler needed at current scale since
    active traffic naturally refreshes the 5-min TTL.
    """
    from services.claude import get_client
    from services.style_profiles import get_style_anchors

    client = get_client()

    for lang in ("en", "te", "hi"):
        try:
            style_anchors = await get_style_anchors(lang)
            static = SYSTEM_PERSONA
            if style_anchors:
                static += f"\n\n{style_anchors}"

            system_blocks = [
                {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}}
            ]

            async with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1,
                system=system_blocks,
                messages=[{"role": "user", "content": "."}],
            ) as stream:
                async for _ in stream.text_stream:
                    pass

            _log.info("[cache_warmer] Sonnet cache warmed lang=%s", lang)
        except Exception as e:
            _log.warning("[cache_warmer] warmup failed lang=%s: %r", lang, e)
