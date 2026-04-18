import anthropic

QUALITY_PROMPT = """Rate the quality of this transcript chunk for an AI knowledge base.
Score 1-5 where:
5 = Clear, informative, directly useful content
3 = Adequate but generic or partially useful
1 = Noise, gibberish, or completely uninformative

Respond with only a single digit 1-5.

Chunk: {chunk}"""


async def score_chunks(chunks: list[str], client: anthropic.AsyncAnthropic) -> list[float]:
    scores = []
    for chunk in chunks:
        try:
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": QUALITY_PROMPT.format(chunk=chunk[:500])}],
            )
            score = float(response.content[0].text.strip())
            scores.append(max(1.0, min(5.0, score)))
        except Exception:
            scores.append(3.0)
    return scores
