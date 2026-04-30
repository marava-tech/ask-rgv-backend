import asyncio
import json
import logging
import re
import subprocess
import tempfile

from db.pool import get_pool
from services.claude import get_client

logger = logging.getLogger(__name__)

HAIKU_PROMPT = """\
Extract 30 short quotes (1-3 sentences each) from this RGV transcript \
that best represent his characteristic speaking style in {language_name}.

Focus on:
- Sentence starters he reuses
- Rhetorical questions he favors
- His vocabulary for abstract concepts (fear, truth, reality, ego, success)
- How he transitions between ideas
- Phrases that sound distinctly like him vs generic speech

Rules:
- Verbatim quotes only — do not paraphrase
- Skip interviewer questions or other speakers
- Skip generic filler ("um", "you know", timestamps)
- Each quote must be complete (don't cut mid-sentence)

Return a JSON array of strings. No commentary, no keys — just the array.

Transcript:
{transcript}"""

LANGUAGE_NAMES = {"en": "English", "te": "Telugu", "hi": "Hindi"}


def _ytdlp_transcript(url: str, language: str) -> str | None:
    lang_opts = f"{language},en" if language != "en" else "en"
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(
            ["yt-dlp", "--write-subs", "--write-auto-subs", "--skip-download",
             "--sub-lang", lang_opts, "--sub-format", "vtt",
             "--output", f"{tmpdir}/%(id)s", url],
            capture_output=True, timeout=60,
        )
        import glob
        vtt_files = glob.glob(f"{tmpdir}/*.vtt")
        if not vtt_files:
            return None
        return _parse_vtt(vtt_files[0])


def _parse_vtt(path: str) -> str:
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("WEBVTT") or "-->" in line or line.isdigit():
                continue
            line = re.sub(r"<[^>]+>", "", line)
            lines.append(line)
    return " ".join(lines)


async def extract_style_profiles_for_video(video_id: str) -> int:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT source_url, source_language FROM ingestion_log WHERE video_id = $1 AND status = 'complete'",
        video_id,
    )
    if not row:
        logger.warning("[style_extract] video_id=%s not found or not complete", video_id)
        return 0

    url = row["source_url"]
    language = row["source_language"]
    language_name = LANGUAGE_NAMES.get(language, "English")

    loop = asyncio.get_event_loop()
    transcript = await loop.run_in_executor(None, _ytdlp_transcript, url, language)

    if not transcript or len(transcript.strip()) < 200:
        logger.warning("[style_extract] no transcript for video_id=%s", video_id)
        return 0

    client = get_client()
    prompt = HAIKU_PROMPT.format(
        language_name=language_name,
        transcript=transcript[:12000],
    )
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        quotes = json.loads(raw)
        quotes = [q for q in quotes if isinstance(q, str) and len(q.strip()) > 10]
    except json.JSONDecodeError:
        logger.warning("[style_extract] JSON parse error for video_id=%s", video_id)
        return 0

    inserted = 0
    for quote in quotes:
        try:
            await pool.execute(
                "INSERT INTO rgv_style_profiles (language, quote, source_video_id) VALUES ($1, $2, $3)",
                language, quote.strip(), video_id,
            )
            inserted += 1
        except Exception:
            pass

    logger.info("[style_extract] video_id=%s inserted=%d quotes", video_id, inserted)
    return inserted
