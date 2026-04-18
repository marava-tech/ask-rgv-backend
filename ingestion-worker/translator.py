import anthropic

TRANSLATE_PROMPT = """Translate the following {language} transcript to English.
Preserve the speaker's meaning and tone. Do not add commentary.
Return only the translated text.

Transcript:
{text}"""

LANGUAGE_NAMES = {"te": "Telugu", "hi": "Hindi"}


async def translate_to_english(text: str, source_language: str, client: anthropic.AsyncAnthropic) -> str:
    if source_language == "en":
        return text
    lang_name = LANGUAGE_NAMES.get(source_language, source_language)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        messages=[{
            "role": "user",
            "content": TRANSLATE_PROMPT.format(language=lang_name, text=text[:15000]),
        }],
    )
    return response.content[0].text
