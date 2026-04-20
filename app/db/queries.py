from datetime import datetime
from uuid import UUID

import asyncpg

from db.pool import get_pool


# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(google_id: str, email: str, display_name: str, avatar_url: str) -> dict:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO users (google_id, email, display_name, avatar_url)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (google_id) DO UPDATE
            SET email = EXCLUDED.email,
                display_name = EXCLUDED.display_name,
                avatar_url = EXCLUDED.avatar_url
        RETURNING id, tier
        """,
        google_id, email, display_name, avatar_url,
    )
    return dict(row)


async def get_user_by_id(user_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM users WHERE id = $1", UUID(user_id))
    return dict(row) if row else None


async def update_user_tier(user_id: str, tier: str) -> None:
    pool = get_pool()
    await pool.execute("UPDATE users SET tier = $1 WHERE id = $2", tier, UUID(user_id))


# ── Refresh tokens ────────────────────────────────────────────────────────────

async def store_refresh_token(user_id: str, token_hash: str, expires_at: datetime) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES ($1, $2, $3)",
            UUID(user_id), token_hash, expires_at,
        )
        # Bug #18: tokens accumulated unboundedly per user; cap at 10, delete oldest beyond that
        await conn.execute(
            """
            DELETE FROM refresh_tokens WHERE id IN (
                SELECT id FROM refresh_tokens WHERE user_id = $1
                ORDER BY created_at DESC OFFSET 10
            )
            """,
            UUID(user_id),
        )


async def consume_refresh_token(token_hash: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        DELETE FROM refresh_tokens WHERE token_hash = $1 AND expires_at > now()
        RETURNING user_id
        """,
        token_hash,
    )
    return dict(row) if row else None


async def delete_user_refresh_tokens(user_id: str) -> None:
    pool = get_pool()
    await pool.execute("DELETE FROM refresh_tokens WHERE user_id = $1", UUID(user_id))


async def delete_refresh_token_by_hash(token_hash: str) -> None:
    pool = get_pool()
    await pool.execute("DELETE FROM refresh_tokens WHERE token_hash = $1", token_hash)


# ── Sessions ──────────────────────────────────────────────────────────────────

async def create_session(user_id: str | None, device_id: str | None) -> str:
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO sessions (user_id, device_id) VALUES ($1, $2) RETURNING id",
        UUID(user_id) if user_id else None, device_id,
    )
    return str(row["id"])


async def get_session(session_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT id, user_id FROM sessions WHERE id = $1", UUID(session_id))
    return dict(row) if row else None


async def update_session_language(session_id: str, language: str) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE sessions SET language = $1 WHERE id = $2",
        language, UUID(session_id),
    )


async def update_session_title(session_id: str, title: str) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE sessions SET session_title = $1 WHERE id = $2 AND session_title IS NULL",
        title, UUID(session_id),
    )


async def delete_session(session_id: str, user_id: str) -> bool:
    pool = get_pool()
    result = await pool.execute(
        "DELETE FROM sessions WHERE id = $1 AND user_id = $2",
        UUID(session_id), UUID(user_id),
    )
    return result == "DELETE 1"


async def rename_session(session_id: str, user_id: str, title: str) -> bool:
    pool = get_pool()
    result = await pool.execute(
        "UPDATE sessions SET session_title = $1 WHERE id = $2 AND user_id = $3",
        title.strip()[:100], UUID(session_id), UUID(user_id),
    )
    return result == "UPDATE 1"


async def end_session(session_id: str, session_title: str | None = None) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE sessions SET ended_at = now(), session_title = COALESCE($2, session_title) WHERE id = $1",
        UUID(session_id), session_title,
    )


async def get_user_sessions(
    user_id: str,
    limit: int = 20,
    offset: int = 0,
    query: str | None = None,
) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id, session_title AS title, turn_count, language, started_at, ended_at
        FROM sessions
        WHERE user_id = $1
          AND ($4::text IS NULL OR $4 = '' OR COALESCE(session_title, '') ILIKE '%' || $4 || '%')
        ORDER BY started_at DESC LIMIT $2 OFFSET $3
        """,
        UUID(user_id), limit, offset, query,
    )
    return [dict(r) for r in rows]


# ── Turns ─────────────────────────────────────────────────────────────────────

async def store_turn(
    session_id: str, mode: str, user_input: str, response: str,
    tokens_used: int, latency_ms: int, rag_chunks_used: int,
) -> tuple[str, int]:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO turns (session_id, mode, user_input, response, tokens_used, latency_ms, rag_chunks_used)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        UUID(session_id), mode, user_input, response, tokens_used, latency_ms, rag_chunks_used,
    )
    updated = await pool.fetchrow(
        "UPDATE sessions SET turn_count = turn_count + 1 WHERE id = $1 RETURNING turn_count",
        UUID(session_id),
    )
    return str(row["id"]), updated["turn_count"]


async def update_turn_audio_seconds(turn_id: str, played_seconds: int, user_id: str) -> None:
    pool = get_pool()
    # Bug #5: no ownership check — any authenticated user could overwrite another user's turn data
    await pool.execute(
        """
        UPDATE turns SET audio_played_seconds = $1
        WHERE id = $2 AND session_id IN (SELECT id FROM sessions WHERE user_id = $3)
        """,
        played_seconds, UUID(turn_id), UUID(user_id),
    )


async def get_session_turns(session_id: str) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM turns WHERE session_id = $1 ORDER BY created_at ASC",
        UUID(session_id),
    )
    return [dict(r) for r in rows]


# ── Subscriptions ─────────────────────────────────────────────────────────────

async def get_active_subscription(user_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM subscriptions
        WHERE user_id = $1 AND status = 'active' AND current_period_end > now()
        ORDER BY created_at DESC LIMIT 1
        """,
        UUID(user_id),
    )
    return dict(row) if row else None


async def get_subscription_by_order_id(order_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, user_id, tier, status FROM subscriptions WHERE razorpay_order_id = $1",
        order_id,
    )
    return dict(row) if row else None


async def create_subscription_order(user_id: str, tier: str, order_id: str) -> str:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO subscriptions (user_id, tier, razorpay_order_id, status)
        VALUES ($1, $2, $3, 'pending')
        RETURNING id
        """,
        UUID(user_id), tier, order_id,
    )
    return str(row["id"])


async def activate_subscription(payment_id: str, order_id: str) -> dict | None:
    pool = get_pool()
    # Bug #15A: no status='pending' guard — duplicate webhook kept resetting period_end
    # Bug #15B: updated_at never set
    # Use GREATEST to extend existing period rather than overwrite it
    row = await pool.fetchrow(
        """
        UPDATE subscriptions
        SET status = 'active',
            razorpay_payment_id = $1,
            current_period_end = GREATEST(COALESCE(current_period_end, now()), now()) + interval '30 days',
            updated_at = now()
        WHERE razorpay_order_id = $2 AND status IN ('pending', 'active')
        RETURNING user_id, tier
        """,
        payment_id, order_id,
    )
    return dict(row) if row else None


# ── Style profiles ────────────────────────────────────────────────────────────

async def get_style_profiles(language: str, limit: int = 20) -> list[str]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT quote FROM rgv_style_profiles WHERE language = $1 AND active = true ORDER BY random() LIMIT $2",
        language, limit,
    )
    return [r["quote"] for r in rows]


# ── Crisis events ─────────────────────────────────────────────────────────────

async def log_crisis_event(session_id: str | None, trigger_phrase: str) -> None:
    pool = get_pool()
    await pool.execute(
        "INSERT INTO crisis_events (session_id, trigger_phrase) VALUES ($1, $2)",
        UUID(session_id) if session_id else None, trigger_phrase,
    )


# ── Quotes ────────────────────────────────────────────────────────────────────

async def get_quote_of_day(language: str = "en") -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, text, source FROM quotes WHERE active = true AND language = $1 ORDER BY random() LIMIT 1",
        language,
    )
    return dict(row) if row else None


async def get_quote_of_day_except(language: str, exclude_id: str | None) -> dict | None:
    pool = get_pool()
    if not exclude_id:
        return await get_quote_of_day(language)
    try:
        from uuid import UUID
        exclude_uuid = UUID(exclude_id)
    except (ValueError, AttributeError):
        return await get_quote_of_day(language)
    row = await pool.fetchrow(
        "SELECT id, text, source FROM quotes WHERE active = true AND language = $1 AND id != $2 ORDER BY random() LIMIT 1",
        language, exclude_uuid,
    )
    return dict(row) if row else None


async def list_quotes_for_admin(language: str | None = None, limit: int = 500) -> list[dict]:
    pool = get_pool()
    if language:
        rows = await pool.fetch(
            """
            SELECT id, text, source, language, active, created_at
            FROM quotes
            WHERE language = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            language,
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, text, source, language, active, created_at
            FROM quotes
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def insert_quote(text: str, source: str | None, language: str) -> dict:
    pool = get_pool()
    src = (source or "").strip() or None
    row = await pool.fetchrow(
        """
        INSERT INTO quotes (text, source, language)
        VALUES ($1, $2, $3)
        RETURNING id, text, source, language, active, created_at
        """,
        text.strip(),
        src,
        language,
    )
    return dict(row)


async def set_quote_active(quote_id: str, active: bool) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE quotes SET active = $1 WHERE id = $2",
        active,
        UUID(quote_id),
    )


# ── Ingestion log ─────────────────────────────────────────────────────────────

async def create_ingestion_job(source_url: str, video_id: str, source_language: str) -> str:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO ingestion_log (source_url, video_id, source_language)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        source_url, video_id, source_language,
    )
    return str(row["id"])


async def get_ingestion_job(job_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM ingestion_log WHERE id = $1", UUID(job_id))
    return dict(row) if row else None


async def list_ingestion_jobs() -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM ingestion_log ORDER BY created_at DESC"
    )
    return [dict(r) for r in rows]


async def toggle_ingestion_job(job_id: str, enabled: bool) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE ingestion_log SET enabled = $1 WHERE id = $2",
        enabled, UUID(job_id),
    )


# ── Admin stats ───────────────────────────────────────────────────────────────

async def get_admin_stats() -> dict:
    pool = get_pool()
    active_users = await pool.fetchval(
        "SELECT COUNT(DISTINCT user_id) FROM sessions WHERE started_at > now() - interval '1 day'"
    )
    total_sessions = await pool.fetchval("SELECT COUNT(*) FROM sessions")
    total_turns = await pool.fetchval("SELECT COUNT(*) FROM turns")
    # Bug #45: AVG over all turns grew unbounded; restrict to last 7 days
    avg_latency = await pool.fetchval(
        "SELECT AVG(latency_ms) FROM turns WHERE latency_ms IS NOT NULL AND created_at > now() - interval '7 days'"
    )
    return {
        "active_users_today": active_users,
        "total_sessions": total_sessions,
        "total_turns": total_turns,
        "avg_latency_ms": round(avg_latency) if avg_latency else None,
    }
