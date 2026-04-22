import asyncio
import anthropic

TRANSLATE_PROMPT = """Translate the following {language} transcript chunk to English.
Preserve the speaker's meaning and tone. Do not add commentary.
Return only the translated text.

Transcript:
{text}"""

LANGUAGE_NAMES = {"te": "Telugu", "hi": "Hindi"}
_CHUNK_SIZE = 12_000  # chars — fits comfortably within Haiku 8192-token output


async def _translate_chunk(text: str, lang_name: str, client: anthropic.AsyncAnthropic) -> str:
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        messages=[{
            "role": "user",
            "content": TRANSLATE_PROMPT.format(language=lang_name, text=text),
        }],
    )
    return response.content[0].text


async def translate_to_english(text: str, source_language: str, client: anthropic.AsyncAnthropic) -> str:
    if source_language == "en":
        return text
    lang_name = LANGUAGE_NAMES.get(source_language, source_language)

    # C-8: split into chunks so long videos (60-100k chars) don't lose 75%+ of transcript
    chunks = [text[i:i + _CHUNK_SIZE] for i in range(0, len(text), _CHUNK_SIZE)]
    translated_chunks = await asyncio.gather(
        *[_translate_chunk(chunk, lang_name, client) for chunk in chunks]
    )
    return " ".join(translated_chunks)
