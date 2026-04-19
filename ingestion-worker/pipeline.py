import uuid
from datetime import datetime, timezone
import anthropic
import asyncpg

from chunker import chunk_text
from embedder import embed_chunks
from quality import score_chunks
from qdrant_writer import ensure_collection, upsert_chunks
from transcript import extract_video_id, get_video_title, whisper_transcript, ytdlp_transcript
from translator import translate_to_english


async def _update(pool: asyncpg.Pool, job_id: str, **kwargs) -> None:
    fields = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(kwargs))
    values = list(kwargs.values())
    await pool.execute(
        f"UPDATE ingestion_log SET {fields} WHERE id = $1",
        uuid.UUID(job_id), *values,
    )


async def run_pipeline(job_id: str, url: str, source_language: str, pool: asyncpg.Pool, settings) -> None:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    video_id = extract_video_id(url)

    # Bug #16: ON CONFLICT keeps the original row id, not the new job_id passed in;
    # use RETURNING id to get the real id so subsequent _update calls hit the correct row
    row = await pool.fetchrow(
        """
        INSERT INTO ingestion_log (id, source_url, video_id, source_language, status, current_step, progress_pct)
        VALUES ($1, $2, $3, $4, 'extracting_transcript', 'Checking YouTube for captions (yt-dlp)...', 10)
        ON CONFLICT (video_id) WHERE video_id IS NOT NULL DO UPDATE SET
            status = 'extracting_transcript', current_step = 'Checking YouTube for captions (yt-dlp)...',
            progress_pct = 10, error_message = NULL
        RETURNING id
        """,
        uuid.UUID(job_id), url, video_id, source_language,
    )
    job_id = str(row["id"])  # Use actual DB id — may differ from caller's UUID on re-ingest

    try:
        # STEP 1: transcript
        transcript_method = "ytdlp"
        raw_transcript = ytdlp_transcript(url, source_language)

        if raw_transcript:
            await _update(pool, job_id, progress_pct=30, current_step="Captions found ✓")
        else:
            await _update(pool, job_id, progress_pct=15, current_step="No captions found. Downloading audio...")
            transcript_method = "whisper"
            raw_transcript = whisper_transcript(url, source_language)
            await _update(pool, job_id, progress_pct=30, current_step="Transcription complete ✓", transcript_method="whisper")

        if not raw_transcript:
            raise ValueError("Could not extract transcript via yt-dlp or Whisper")

        await _update(pool, job_id, transcript_method=transcript_method)

        source_title = get_video_title(url)
        await _update(pool, job_id, source_title=source_title)

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
        # Bug #30: datetime.utcnow() is naive and deprecated in Python 3.12;
        # use timezone-aware datetime.now(timezone.utc) instead
        await pool.execute(
            "UPDATE ingestion_log SET ingested_at = $1 WHERE id = $2",
            datetime.now(timezone.utc), uuid.UUID(job_id),
        )

    except Exception as e:
        msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        await _update(pool, job_id, status="failed", current_step="Failed", error_message=msg)
