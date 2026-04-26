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
    row = await pool.fetchrow(
        "SELECT id, google_id, email, display_name, avatar_url, tier, created_at FROM users WHERE id = $1",
        UUID(user_id),
    )
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


async def get_session_language_from_db(session_id: str) -> str | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT language FROM sessions WHERE id = $1", UUID(session_id))
    return row["language"] if row and row["language"] else None


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
    turn_id: str | None = None,
) -> None:
    pool = get_pool()
    if turn_id:
        await pool.execute(
            """
            INSERT INTO turns (id, session_id, mode, user_input, response, tokens_used, latency_ms, rag_chunks_used)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO NOTHING
            """,
            UUID(turn_id), UUID(session_id), mode, user_input, response, tokens_used, latency_ms, rag_chunks_used,
        )
    else:
        await pool.execute(
            """
            INSERT INTO turns (session_id, mode, user_input, response, tokens_used, latency_ms, rag_chunks_used)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            UUID(session_id), mode, user_input, response, tokens_used, latency_ms, rag_chunks_used,
        )
    await pool.execute(
        "UPDATE sessions SET turn_count = turn_count + 1 WHERE id = $1",
        UUID(session_id),
    )


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


async def get_session_turns(
    session_id: str,
    limit: int = 200,
    before: datetime | None = None,
) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id, session_id, mode, user_input, response, tokens_used, latency_ms,
               rag_chunks_used, audio_played_seconds, created_at
        FROM turns
        WHERE session_id = $1
          AND ($3::timestamptz IS NULL OR created_at < $3)
        ORDER BY created_at DESC LIMIT $2
        """,
        UUID(session_id), limit, before,
    )
    return [dict(r) for r in reversed(rows)]


# ── Subscriptions ─────────────────────────────────────────────────────────────

async def get_active_subscription(user_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, user_id, tier, status, razorpay_order_id, razorpay_payment_id,
               current_period_end, created_at, updated_at
        FROM subscriptions
        WHERE user_id = $1 AND status = 'active' AND current_period_end > now()
        ORDER BY created_at DESC LIMIT 1
        """,
        UUID(user_id),
    )
    return dict(row) if row else None


async def get_subscription_by_order_id(order_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, user_id, tier, status, expected_amount_paise FROM subscriptions WHERE razorpay_order_id = $1",
        order_id,
    )
    return dict(row) if row else None


async def create_subscription_order(
    user_id: str, tier: str, order_id: str, expected_amount_paise: int | None = None
) -> str:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO subscriptions (user_id, tier, razorpay_order_id, status, expected_amount_paise)
        VALUES ($1, $2, $3, 'pending', $4)
        RETURNING id
        """,
        UUID(user_id), tier, order_id, expected_amount_paise,
    )
    return str(row["id"])


async def activate_subscription_and_update_tier(payment_id: str, order_id: str) -> dict | None:
    """Activate a pending subscription and update the user's tier in one atomic transaction."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                WITH activated AS (
                    UPDATE subscriptions
                    SET status = 'active',
                        razorpay_payment_id = $1,
                        current_period_end = GREATEST(COALESCE(current_period_end, now()), now()) + interval '30 days',
                        updated_at = now()
                    WHERE razorpay_order_id = $2 AND status = 'pending'
                    RETURNING user_id, tier
                )
                UPDATE users
                SET tier = activated.tier
                FROM activated
                WHERE users.id = activated.user_id
                RETURNING activated.user_id AS user_id, activated.tier AS tier
                """,
                payment_id, order_id,
            )
    return dict(row) if row else None


async def expire_ended_subscriptions() -> int:
    """Mark active subscriptions whose period has ended as 'expired' and
    downgrade the corresponding users to 'free'.  Runs in a single transaction
    so users and subscriptions never diverge.  Returns the number of users
    downgraded."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            tag = await conn.execute(
                """
                WITH expired AS (
                    UPDATE subscriptions
                       SET status     = 'expired',
                           updated_at = now()
                     WHERE status = 'active'
                       AND current_period_end < now()
                    RETURNING user_id
                )
                UPDATE users
                   SET tier = 'free'
                 WHERE id IN (SELECT user_id FROM expired)
                   AND tier IN ('fan', 'super_fan')
                """
            )
    # asyncpg returns "UPDATE N" for the last statement in the CTE
    try:
        return int(tag.split()[-1])
    except (AttributeError, ValueError, IndexError):
        return 0


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


# ── Admin: user list ──────────────────────────────────────────────────────────

async def list_admin_users(limit: int = 100, offset: int = 0) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT
            u.id,
            u.email,
            u.display_name,
            u.tier,
            u.created_at,
            COUNT(DISTINCT s.id)::int               AS total_sessions,
            COALESCE(SUM(t.tokens_used), 0)::bigint AS total_tokens,
            MAX(s.started_at)                        AS last_active,
            sub.current_period_end                   AS subscription_expires
        FROM users u
        LEFT JOIN sessions s ON s.user_id = u.id
        LEFT JOIN turns    t ON t.session_id = s.id
        LEFT JOIN LATERAL (
            SELECT current_period_end
            FROM subscriptions
            WHERE user_id = u.id AND status = 'active' AND current_period_end > now()
            ORDER BY current_period_end DESC
            LIMIT 1
        ) sub ON true
        GROUP BY u.id, sub.current_period_end
        ORDER BY u.created_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit, offset,
    )
    return [dict(r) for r in rows]


# ── Bug reports ───────────────────────────────────────────────────────────────

async def insert_bug_report(
    user_id: str | None,
    description: str,
    screenshot_url: str | None,
    device_info: dict | None,
) -> None:
    pool = get_pool()
    import json
    await pool.execute(
        """
        INSERT INTO bug_reports (user_id, description, screenshot_url, device_info, created_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        user_id,
        description,
        screenshot_url,
        json.dumps(device_info) if device_info else None,
        __import__('datetime').datetime.now(__import__('datetime').timezone.utc),
    )


async def get_bug_reports(limit: int = 50, offset: int = 0, status: str | None = None) -> list:
    pool = get_pool()
    if status:
        rows = await pool.fetch(
            """
            SELECT br.id, br.user_id, u.email AS user_email, br.description,
                   br.screenshot_url, br.device_info, br.status, br.admin_notes,
                   br.created_at, br.resolved_at
            FROM bug_reports br
            LEFT JOIN users u ON u.id = br.user_id
            WHERE br.status = $3
            ORDER BY br.created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset, status,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT br.id, br.user_id, u.email AS user_email, br.description,
                   br.screenshot_url, br.device_info, br.status, br.admin_notes,
                   br.created_at, br.resolved_at
            FROM bug_reports br
            LEFT JOIN users u ON u.id = br.user_id
            ORDER BY br.created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
    return [dict(r) for r in rows]


async def count_bug_reports(status: str | None = None) -> int:
    pool = get_pool()
    if status:
        return await pool.fetchval(
            "SELECT COUNT(*) FROM bug_reports WHERE status = $1",
            status,
        )
    return await pool.fetchval("SELECT COUNT(*) FROM bug_reports")


async def get_bug_report_by_id(bug_id: int) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT br.id, br.user_id, u.email AS user_email, br.description,
               br.screenshot_url, br.device_info, br.status, br.admin_notes,
               br.created_at, br.resolved_at
        FROM bug_reports br
        LEFT JOIN users u ON u.id = br.user_id
        WHERE br.id = $1
        """,
        bug_id,
    )
    return dict(row) if row else None


async def update_bug_report(
    bug_id: int,
    status: str | None,
    admin_notes: str | None,
    resolved_by: str | None,
) -> dict | None:
    import datetime
    pool = get_pool()
    sets: list[str] = []
    params: list = []
    idx = 1

    if status is not None:
        sets.append(f"status = ${idx}")
        params.append(status)
        idx += 1
        if status in ("resolved", "wont_fix"):
            sets.append(f"resolved_at = ${idx}")
            params.append(datetime.datetime.now(datetime.timezone.utc))
            idx += 1
            sets.append(f"resolved_by = ${idx}")
            params.append(resolved_by)
            idx += 1
        else:
            sets.extend([f"resolved_at = ${idx}", f"resolved_by = ${idx + 1}"])
            params.extend([None, None])
            idx += 2

    if admin_notes is not None:
        sets.append(f"admin_notes = ${idx}")
        params.append(admin_notes)
        idx += 1

    if not sets:
        return await get_bug_report_by_id(bug_id)

    params.append(bug_id)
    row = await pool.fetchrow(
        f"""
        UPDATE bug_reports SET {', '.join(sets)}
        WHERE id = ${idx}
        RETURNING id, user_id, description, screenshot_url, device_info,
                  status, admin_notes, created_at, resolved_at
        """,
        *params,
    )
    return dict(row) if row else None
