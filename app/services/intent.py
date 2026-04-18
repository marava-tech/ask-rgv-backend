from services.claude import haiku_call

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
    except Exception:
        pass
    return "seeking_clarity"
