import uuid
from datetime import datetime, timezone
import anthropic
import asyncpg

from chunker import chunk_text
from embedder import embed_chunks
from quality import score_chunks
from qdrant_writer import ensure_collection, upsert_chunks
from transcript import (
    extract_video_id,
    get_video_title,
    invidious_audio_whisper,
    invidious_transcript,
    whisper_transcript,
    ytta_transcript,
    ytdlp_transcript,
)
from translator import translate_to_english


async def _update(pool: asyncpg.Pool, job_id: str, **kwargs) -> None:
    fields = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(kwargs))
    values = list(kwargs.values())
    await pool.execute(
        f"UPDATE ingestion_log SET {fields} WHERE id = $1",
        uuid.UUID(job_id), *values,
    )


async def _extract_transcript(
    job_id: str,
    url: str,
    video_id: str,
    source_language: str,
    initial_failed: list[str],
    pool: asyncpg.Pool,
) -> tuple[str, str]:
    """
    Try transcript methods in order, skipping methods that previously failed.
    Returns (raw_transcript, transcript_method).
    Raises ValueError if all methods fail.
    """
    failed_methods = list(initial_failed)

    methods = [
        ("ytta",           lambda: ytta_transcript(video_id, source_language)),
        ("invidious_caps", lambda: invidious_transcript(video_id, source_language)),
        ("ytdlp",          lambda: ytdlp_transcript(url, source_language)),
        ("invidious_audio", lambda: invidious_audio_whisper(video_id, source_language)),
        ("whisper",        lambda: whisper_transcript(url, source_language)),
    ]

    for method_name, method_fn in methods:
        if method_name in failed_methods:
            continue
        try:
            result = method_fn()
            if result:
                return result, method_name
            # Empty result — method succeeded but returned nothing (no captions)
            failed_methods.append(method_name)
        except Exception:
            failed_methods.append(method_name)

        await pool.execute(
            "UPDATE ingestion_log SET failed_methods = $1 WHERE id = $2",
            failed_methods, uuid.UUID(job_id),
        )

    raise ValueError(
        "All transcript methods failed. Upload a manual transcript via the dashboard."
    )


async def run_pipeline(job_id: str, url: str, source_language: str, pool: asyncpg.Pool, settings) -> None:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    video_id = extract_video_id(url)

    row = await pool.fetchrow(
        """
        INSERT INTO ingestion_log (id, source_url, video_id, source_language, status, current_step, progress_pct)
        VALUES ($1, $2, $3, $4, 'extracting_transcript', 'Checking YouTube for captions (yt-dlp)...', 10)
        ON CONFLICT (video_id) WHERE video_id IS NOT NULL DO UPDATE SET
            status = 'extracting_transcript', current_step = 'Checking YouTube for captions (yt-dlp)...',
            progress_pct = 10, error_message = NULL, source_language = EXCLUDED.source_language
        RETURNING id, failed_methods
        """,
        uuid.UUID(job_id), url, video_id, source_language,
    )
    job_id = str(row["id"])
    prior_failed = list(row["failed_methods"] or [])

    try:
        await _update(pool, job_id, current_step="Trying transcript sources...")
        raw_transcript, transcript_method = await _extract_transcript(
            job_id, url, video_id, source_language, prior_failed, pool
        )
        await _update(pool, job_id, progress_pct=30,
                      current_step=f"Transcript obtained via {transcript_method} ✓",
                      transcript_method=transcript_method)

        source_title = get_video_title(url)
        await _update(pool, job_id, source_title=source_title)

        await _run_post_transcript(
            job_id, url, video_id, source_language, source_title,
            raw_transcript, client, pool, settings,
        )

    except Exception as e:
        msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        await _update(pool, job_id, status="failed", current_step="Failed", error_message=msg)


async def run_pipeline_from_transcript(
    job_id: str,
    url: str,
    source_language: str,
    transcript_text: str,
    pool: asyncpg.Pool,
    settings,
) -> None:
    """Pipeline entrypoint when the transcript is supplied manually (file upload)."""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    video_id = extract_video_id(url)

    row = await pool.fetchrow(
        """
        INSERT INTO ingestion_log (id, source_url, video_id, source_language, status, current_step, progress_pct, transcript_method)
        VALUES ($1, $2, $3, $4, 'translating', 'Manual transcript received ✓', 30, 'manual_upload')
        ON CONFLICT (video_id) WHERE video_id IS NOT NULL DO UPDATE SET
            status = 'translating', current_step = 'Manual transcript received ✓',
            progress_pct = 30, error_message = NULL,
            source_language = EXCLUDED.source_language,
            transcript_method = 'manual_upload'
        RETURNING id
        """,
        uuid.UUID(job_id), url, video_id, source_language,
    )
    job_id = str(row["id"])

    try:
        source_title = get_video_title(url)
        await _update(pool, job_id, source_title=source_title)

        await _run_post_transcript(
            job_id, url, video_id, source_language, source_title,
            transcript_text, client, pool, settings,
        )
    except Exception as e:
        msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        await _update(pool, job_id, status="failed", current_step="Failed", error_message=msg)


async def _run_post_transcript(
    job_id: str,
    url: str,
    video_id: str,
    source_language: str,
    source_title: str,
    raw_transcript: str,
    client: anthropic.AsyncAnthropic,
    pool: asyncpg.Pool,
    settings,
) -> None:
    """Steps 2-5: translate → chunk → score+embed → store. Shared by both pipeline entrypoints."""

    # STEP 2: translate (skip if English)
    if source_language != "en":
        await _update(pool, job_id, status="translating", progress_pct=40,
                      current_step="Translating transcript to English (Haiku)...")
        english_text = await translate_to_english(raw_transcript, source_language, client)
        await _update(pool, job_id, progress_pct=50, current_step="Translation complete ✓", translated=True)
    else:
        english_text = raw_transcript

    # STEP 3: chunk
    await _update(pool, job_id, status="chunking", progress_pct=55,
                  current_step="Cleaning and splitting transcript into chunks...")
    chunks = chunk_text(english_text)

    if not chunks:
        raise ValueError("No chunks produced after cleaning")

    # STEP 4: quality score + embed
    await _update(pool, job_id, status="embedding", progress_pct=70,
                  current_step="Scoring chunk quality (Haiku)...")
    scores = await score_chunks(chunks, client)
    avg_score = sum(scores) / len(scores)
    quality_flag = avg_score < 2.5

    await _update(pool, job_id, progress_pct=80, current_step="Generating embeddings (BGE-M3)...",
                  translation_quality_flag=quality_flag)
    embeddings = await embed_chunks(chunks, settings.embedding_service_url)

    # STEP 5: store
    await _update(pool, job_id, current_step="Storing in Qdrant...")
    await ensure_collection(settings.qdrant_url, settings.qdrant_collection)
    await upsert_chunks(
        chunks=chunks,
        embeddings=embeddings,
        scores=scores,
        video_id=video_id,
        source_title=source_title,
        source_url=url,
        source_language=source_language,
        qdrant_url=settings.qdrant_url,
        collection=settings.qdrant_collection,
    )

    await _update(pool, job_id, status="complete", progress_pct=100,
                  current_step="Done ✓", chunk_count=len(chunks))
    await pool.execute(
        "UPDATE ingestion_log SET ingested_at = $1 WHERE id = $2",
        datetime.now(timezone.utc), uuid.UUID(job_id),
    )
