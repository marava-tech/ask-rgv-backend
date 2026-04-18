import re
import tiktoken

CHUNK_TOKENS = 600
OVERLAP_TOKENS = 100
MIN_TOKENS = 200

_enc = tiktoken.get_encoding("cl100k_base")

NOISE_PATTERNS = [
    r"\[Music\]", r"\[Laughter\]", r"\[Applause\]", r"\[.*?\]",
    r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}",
    r"^\d+$",
    r"<[^>]+>",
]


def clean_text(text: str) -> str:
    for pattern in NOISE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def chunk_text(text: str) -> list[str]:
    text = clean_text(text)
    tokens = _enc.encode(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = start + CHUNK_TOKENS
        chunk_tokens = tokens[start:end]
        if len(chunk_tokens) >= MIN_TOKENS:
            chunks.append(_enc.decode(chunk_tokens))
        start += CHUNK_TOKENS - OVERLAP_TOKENS
    return chunks
