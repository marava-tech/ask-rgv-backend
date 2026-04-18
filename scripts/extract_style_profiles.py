#!/usr/bin/env python3
"""
Phase 1.5 — Style Profile Extraction

Extracts verbatim RGV-style quotes from raw (untranslated) transcripts
and populates the rgv_style_profiles table.

Usage:
  # Extract for all languages (recommended first run):
  python scripts/extract_style_profiles.py

  # Extract for a specific language only:
  python scripts/extract_style_profiles.py --language en
  python scripts/extract_style_profiles.py --language te
  python scripts/extract_style_profiles.py --language hi

  # Limit videos processed per language (for testing):
  python scripts/extract_style_profiles.py --language en --limit 3

  # Clear existing profiles before re-running:
  python scripts/extract_style_profiles.py --language en --clear

Run from inside the backend container or locally with DATABASE_URL and ANTHROPIC_API_KEY set.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile

import anthropic
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://askrgv:Madhu7814@localhost:5432/askrgv")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

VIDEOS_PER_LANGUAGE = {"en": 20, "te": 20, "hi": 10}
QUOTES_TARGET = 100

HAIKU_PROMPT = """\
Extract {target} short quotes (1–3 sentences each) from this RGV transcript \
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


def ytdlp_transcript(url: str, language: str) -> str | None:
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
    import re
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("WEBVTT") or "-->" in line or line.isdigit():
                continue
            line = re.sub(r"<[^>]+>", "", line)
            lines.append(line)
    return " ".join(lines)


async def extract_quotes(transcript: str, language: str, client: anthropic.AsyncAnthropic) -> list[str]:
    prompt = HAIKU_PROMPT.format(
        target=QUOTES_TARGET,
        language_name=LANGUAGE_NAMES[language],
        transcript=transcript[:12000],
    )
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        quotes = json.loads(raw)
        return [q for q in quotes if isinstance(q, str) and len(q.strip()) > 10]
    except json.JSONDecodeError:
        print(f"  ⚠ JSON parse error, got: {raw[:200]}")
        return []


async def run(language: str | None, limit: int | None, clear: bool) -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    languages = [language] if language else ["en", "te", "hi"]

    for lang in languages:
        max_videos = limit or VIDEOS_PER_LANGUAGE[lang]
        print(f"\n{'='*60}")
        print(f"Language: {LANGUAGE_NAMES[lang]} ({lang}) — up to {max_videos} videos")
        print("="*60)

        if clear:
            deleted = await pool.fetchval(
                "DELETE FROM rgv_style_profiles WHERE language = $1 RETURNING COUNT(*)", lang
            )
            print(f"  Cleared {deleted or 0} existing profiles")

        # Get top completed videos for this language
        rows = await pool.fetch(
            """
            SELECT video_id, source_url, source_title
            FROM ingestion_log
            WHERE source_language = $1 AND status = 'complete' AND enabled = true
            ORDER BY chunk_count DESC
            LIMIT $2
            """,
            lang, max_videos,
        )

        if not rows:
            print(f"  No completed videos found for {lang}. Run ingestion first.")
            continue

        print(f"  Found {len(rows)} videos to process")
        total_quotes = 0

        for i, row in enumerate(rows, 1):
            video_id = row["video_id"]
            url = row["source_url"]
            title = row["source_title"] or url
            print(f"\n  [{i}/{len(rows)}] {title[:60]}")

            # Check if we already have enough quotes from this video
            existing = await pool.fetchval(
                "SELECT COUNT(*) FROM rgv_style_profiles WHERE source_video_id = $1 AND language = $2",
                video_id, lang,
            )
            if existing and existing >= 10:
                print(f"    → Skipping (already have {existing} quotes from this video)")
                total_quotes += existing
                continue

            print(f"    → Fetching transcript via yt-dlp...")
            transcript = ytdlp_transcript(url, lang)

            if not transcript or len(transcript.strip()) < 200:
                print(f"    → No transcript available, skipping")
                continue

            print(f"    → Transcript: {len(transcript)} chars. Extracting quotes with Haiku...")
            quotes = await extract_quotes(transcript, lang, client)

            if not quotes:
                print(f"    → No quotes extracted")
                continue

            # Insert into DB
            inserted = 0
            for quote in quotes:
                try:
                    await pool.execute(
                        "INSERT INTO rgv_style_profiles (language, quote, source_video_id) VALUES ($1, $2, $3)",
                        lang, quote.strip(), video_id,
                    )
                    inserted += 1
                except asyncpg.UniqueViolationError:
                    pass

            total_quotes += inserted
            print(f"    → Inserted {inserted} quotes (total so far: {total_quotes})")

            if total_quotes >= QUOTES_TARGET * 1.5:
                print(f"  ✓ Reached target ({total_quotes} quotes). Stopping.")
                break

        # Summary
        final_count = await pool.fetchval(
            "SELECT COUNT(*) FROM rgv_style_profiles WHERE language = $1 AND active = true", lang
        )
        print(f"\n  ✅ {LANGUAGE_NAMES[lang]}: {final_count} active profiles in DB")
        if final_count < 50:
            print(f"  ⚠  Low count — consider ingesting more {lang} videos and re-running")

    await pool.close()
    print("\nDone. Run 'python scripts/manage.py style-preview --language en' to verify.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract RGV style profiles from transcripts")
    parser.add_argument("--language", choices=["en", "te", "hi"], help="Language to process (default: all)")
    parser.add_argument("--limit", type=int, help="Max videos to process per language")
    parser.add_argument("--clear", action="store_true", help="Clear existing profiles before running")
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    asyncio.run(run(args.language, args.limit, args.clear))
