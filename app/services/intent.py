import logging
from services.claude import haiku_call

logger = logging.getLogger(__name__)

INTENTS = ("venting", "seeking_validation", "debating", "seeking_clarity")


async def classify_intent(message: str) -> str:
    prompt = (
        "Classify this message into exactly one intent:\n"
        "venting | seeking_validation | debating | seeking_clarity\n\n"
        f"Message: {message}\n\n"
        "Reply with only the intent word."
    )
    try:
        result = (await haiku_call(prompt)).strip().lower()
        if result in INTENTS:
            return result
        # Bug #44: parse failures were swallowed silently — now logged for observability
        logger.warning("[intent] unexpected haiku output %r for message len=%d", result, len(message))
    except Exception as e:
        logger.warning("[intent] haiku_call failed: %r", e)
    return "seeking_clarity"
