from datetime import datetime
from uuid import UUID

import asyncpg

from db.pool import get_pool


# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(google_id: str, email: str, display_name: str, avatar_url: str) -> dict:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO users (google_id, email, display_name, avatar_url, current_streak, longest_streak, last_active_date)
        VALUES ($1, $2, $3, $4, 1, 1, CURRENT_DATE)
        ON CONFLICT (google_id) DO UPDATE
            SET email = EXCLUDED.email,
                display_name = EXCLUDED.display_name,
                avatar_url = EXCLUDED.avatar_url
        RETURNING id, tier, preferred_language, preferred_name, current_streak, longest_streak, last_active_date
        """,
        google_id, email, display_name, avatar_url,
    )
    return dict(row)


async def get_user_by_id(user_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, google_id, email, display_name, avatar_url, tier, preferred_language, preferred_name, fcm_token, created_at, current_streak, longest_streak, last_active_date, unlocked_themes FROM users WHERE id = $1",
        UUID(user_id),
    )
    return dict(row) if row else None


async def update_user_fcm_token(user_id: str, fcm_token: str) -> None:
    pool = get_pool()
    await pool.execute("UPDATE users SET fcm_token = $1 WHERE id = $2", fcm_token, UUID(user_id))


async def get_all_fcm_tokens(language: str | None = None) -> list[str]:
    """Return distinct non-null FCM tokens, optionally filtered by preferred_language."""
    pool = get_pool()
    if language:
        rows = await pool.fetch(
            "SELECT DISTINCT fcm_token FROM users WHERE fcm_token IS NOT NULL AND preferred_language = $1",
            language,
        )
    else:
        rows = await pool.fetch("SELECT DISTINCT fcm_token FROM users WHERE fcm_token IS NOT NULL")
    return [r["fcm_token"] for r in rows]


async def update_user_tier(user_id: str, tier: str) -> None:
    pool = get_pool()
    await pool.execute("UPDATE users SET tier = $1 WHERE id = $2", tier, UUID(user_id))


async def update_user_preferred_language(user_id: str, lang: str) -> None:
    pool = get_pool()
    await pool.execute("UPDATE users SET preferred_language = $1 WHERE id = $2", lang, UUID(user_id))


async def update_user_preferences(user_id: str, preferred_language: str | None, preferred_name: str | None) -> None:
    pool = get_pool()
    if preferred_language is not None and preferred_name is not None:
        await pool.execute(
            "UPDATE users SET preferred_language = $1, preferred_name = $2 WHERE id = $3",
            preferred_language, preferred_name, UUID(user_id),
        )
    elif preferred_language is not None:
        await pool.execute("UPDATE users SET preferred_language = $1 WHERE id = $2", preferred_language, UUID(user_id))
    elif preferred_name is not None:
        await pool.execute("UPDATE users SET preferred_name = $1 WHERE id = $2", preferred_name, UUID(user_id))

async def record_user_activity(user_id: str) -> dict:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        WITH previous AS (
            SELECT
                id,
                current_streak,
                longest_streak,
                last_active_date,
                date_trunc('week', last_active_date::timestamp)::date AS last_active_week,
                date_trunc('week', CURRENT_DATE::timestamp)::date AS current_week
            FROM users
            WHERE id = $1
            FOR UPDATE
        ),
        updated AS (
            UPDATE users AS u
            SET
                current_streak = CASE
                    WHEN p.last_active_week = p.current_week THEN p.current_streak
                    WHEN p.last_active_week = (p.current_week - INTERVAL '1 week')::date THEN p.current_streak + 1
                    ELSE 1
                END,
                longest_streak = GREATEST(
                    p.longest_streak,
                    CASE
                        WHEN p.last_active_week = p.current_week THEN p.current_streak
                        WHEN p.last_active_week = (p.current_week - INTERVAL '1 week')::date THEN p.current_streak + 1
                        ELSE 1
                    END
                ),
                last_active_date = CURRENT_DATE
            FROM previous AS p
            WHERE u.id = p.id
            RETURNING
                u.current_streak,
                u.longest_streak,
                CASE
                    WHEN p.last_active_week = p.current_week THEN FALSE
                    ELSE TRUE
                END AS streak_updated
        )
        SELECT * FROM updated
        """,
        UUID(user_id),
    )
    return dict(row)

async def unlock_user_theme(user_id: str, theme_id: str) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE users 
        SET unlocked_themes = array_append(unlocked_themes, $2)
        WHERE id = $1 AND NOT ($2 = ANY(unlocked_themes))
        """,
        UUID(user_id), theme_id,
    )


# ── Refresh tokens ────────────────────────────────────────────────────────────

async def store_refresh_token(
    user_id: str, token_hash: str, expires_at: datetime, family: str | None = None
) -> str:
    """Insert a refresh token and return its token_family UUID string.

    Pass ``family`` to continue an existing rotation chain (on refresh).
    Omit to start a new family (on login).
    """
    import uuid as _uuid
    token_family = UUID(family) if family else _uuid.uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at, token_family) VALUES ($1, $2, $3, $4)",
            UUID(user_id), token_hash, expires_at, token_family,
        )
        # Cap active (unrevoked) tokens at 10 per user; delete oldest beyond that.
        await conn.execute(
            """
            DELETE FROM refresh_tokens WHERE id IN (
                SELECT id FROM refresh_tokens
                WHERE user_id = $1 AND revoked_at IS NULL
                ORDER BY created_at DESC OFFSET 10
            )
            """,
            UUID(user_id),
        )
        # Prune revoked tokens older than 30 days to keep the table lean.
        await conn.execute(
            "DELETE FROM refresh_tokens WHERE user_id = $1 AND revoked_at < now() - interval '30 days'",
            UUID(user_id),
        )
    return str(token_family)


async def consume_refresh_token(token_hash: str) -> dict | None:
    """Mark a refresh token as revoked and return its metadata.

    Returns:
      ``{user_id, token_family}``             — valid token, successfully consumed.
      ``{user_id, token_family, replayed: True}`` — token was already revoked (replay attack).
      ``None``                                 — token not found or expired.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        # Happy path: claim a valid, unrevoked, unexpired token.
        row = await conn.fetchrow(
            """
            UPDATE refresh_tokens
               SET revoked_at = now()
             WHERE token_hash = $1
               AND revoked_at IS NULL
               AND expires_at > now()
            RETURNING user_id, token_family
            """,
            token_hash,
        )
        if row:
            return {"user_id": row["user_id"], "token_family": str(row["token_family"])}

        # Detect replay: token exists but was already revoked.
        revoked = await conn.fetchrow(
            "SELECT user_id, token_family FROM refresh_tokens WHERE token_hash = $1 AND revoked_at IS NOT NULL",
            token_hash,
        )
        if revoked:
            return {
                "user_id": revoked["user_id"],
                "token_family": str(revoked["token_family"]),
                "replayed": True,
            }
    return None


async def invalidate_token_family(family: str) -> None:
    """Delete all refresh tokens belonging to a family (used on replay detection)."""
    pool = get_pool()
    await pool.execute("DELETE FROM refresh_tokens WHERE token_family = $1", UUID(family))


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
        """
        UPDATE sessions
        SET ended_at = now(),
            session_title = COALESCE($2, session_title),
            duration_seconds = EXTRACT(EPOCH FROM (now() - started_at))::int
        WHERE id = $1
        """,
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
        SELECT id, session_title AS title, turn_count, language, started_at, ended_at, highlight_text
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
    rag_context: list | None = None,
) -> None:
    import json as _json
    pool = get_pool()
    rag_context_json = _json.dumps(rag_context) if rag_context else None
    if turn_id:
        await pool.execute(
            """
            INSERT INTO turns (id, session_id, mode, user_input, response, tokens_used, latency_ms, rag_chunks_used, rag_context)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            UUID(turn_id), UUID(session_id), mode, user_input, response, tokens_used, latency_ms, rag_chunks_used, rag_context_json,
        )
    else:
        await pool.execute(
            """
            INSERT INTO turns (session_id, mode, user_input, response, tokens_used, latency_ms, rag_chunks_used, rag_context)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            UUID(session_id), mode, user_input, response, tokens_used, latency_ms, rag_chunks_used, rag_context_json,
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


async def get_admin_conversations(limit: int = 50) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT s.id AS session_id,
               COALESCE(u.email, 'anonymous') AS user_identifier,
               s.language,
               s.turn_count,
               s.started_at,
               s.session_title
        FROM sessions s
        LEFT JOIN users u ON s.user_id = u.id
        ORDER BY s.started_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def get_admin_conversation_detail(session_id: str) -> dict | None:
    pool = get_pool()
    session_row = await pool.fetchrow(
        """
        SELECT s.id AS session_id,
               COALESCE(u.email, 'anonymous') AS user_identifier,
               s.language,
               s.turn_count,
               s.started_at,
               s.session_title
        FROM sessions s
        LEFT JOIN users u ON s.user_id = u.id
        WHERE s.id = $1
        """,
        UUID(session_id),
    )
    if not session_row:
        return None
    turn_rows = await pool.fetch(
        """
        SELECT user_input, response, rag_chunks_used, rag_context, created_at, latency_ms, tokens_used
        FROM turns
        WHERE session_id = $1
        ORDER BY created_at ASC
        """,
        UUID(session_id),
    )
    import json as _json
    turns = []
    for r in turn_rows:
        t = dict(r)
        if t.get("rag_context") and isinstance(t["rag_context"], str):
            try:
                t["rag_context"] = _json.loads(t["rag_context"])
            except Exception:
                pass
        turns.append(t)
    return {"session": dict(session_row), "turns": turns}


# ── Subscriptions ─────────────────────────────────────────────────────────────

async def get_active_subscription(user_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, user_id, tier, status, google_purchase_token, subscription_period,
               current_period_end, created_at, updated_at
        FROM subscriptions
        WHERE user_id = $1 AND status = 'active' AND current_period_end > now()
        ORDER BY created_at DESC LIMIT 1
        """,
        UUID(user_id),
    )
    return dict(row) if row else None


async def activate_iap_subscription(
    user_id: str,
    tier: str,
    purchase_token: str,
    product_id: str,
    subscription_period: str,
    expiry_time_millis: str | None,
) -> dict | None:
    """Activate or renew a subscription via Google Play IAP token. Atomic: updates subscriptions + users."""
    from datetime import timedelta
    pool = get_pool()
    days = 366 if subscription_period == "annual" else 31
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                WITH upserted AS (
                    INSERT INTO subscriptions
                        (user_id, tier, google_purchase_token, product_id, subscription_period,
                         status, current_period_end)
                    VALUES ($1, $2, $3, $4, $5, 'active', now() + $6)
                    ON CONFLICT (google_purchase_token) WHERE google_purchase_token IS NOT NULL DO UPDATE
                        SET status = 'active',
                            tier = EXCLUDED.tier,
                            subscription_period = EXCLUDED.subscription_period,
                            current_period_end = EXCLUDED.current_period_end,
                            updated_at = now()
                    RETURNING user_id, tier
                )
                UPDATE users SET tier = upserted.tier
                FROM upserted WHERE users.id = upserted.user_id
                RETURNING upserted.user_id AS user_id, upserted.tier AS tier
                """,
                UUID(user_id), tier, purchase_token, product_id, subscription_period,
                timedelta(days=days),
            )
    return dict(row) if row else None


async def activate_iap_subscription_by_token(
    purchase_token: str,
    tier: str,
    product_id: str,
    subscription_period: str,
    expiry_time_millis: str | None,
) -> dict | None:
    """Renew/activate by token (from RTDN webhook — no user_id needed, token identifies subscription)."""
    from datetime import timedelta
    days = 366 if subscription_period == "annual" else 31
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                WITH updated AS (
                    UPDATE subscriptions
                    SET status = 'active',
                        tier = $2,
                        subscription_period = $3,
                        current_period_end = now() + $4,
                        updated_at = now()
                    WHERE google_purchase_token = $1
                    RETURNING user_id, tier
                )
                UPDATE users SET tier = updated.tier
                FROM updated WHERE users.id = updated.user_id
                RETURNING updated.user_id AS user_id, updated.tier AS tier
                """,
                purchase_token, tier, subscription_period, timedelta(days=days),
            )
    return dict(row) if row else None


async def expire_subscription_by_token(purchase_token: str) -> None:
    """Mark a subscription as expired by purchase token (from RTDN cancel/expire event)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                WITH expired AS (
                    UPDATE subscriptions
                    SET status = 'expired', updated_at = now()
                    WHERE google_purchase_token = $1 AND status = 'active'
                    RETURNING user_id
                )
                UPDATE users SET tier = 'seeker'
                WHERE id IN (SELECT user_id FROM expired)
                  AND tier IN ('fan', 'super_fan')
                """,
                purchase_token,
            )


async def demote_user_subscription(user_id: str) -> bool:
    """Manually demote a user: expire all active subscriptions and reset tier to seeker."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Mark all active subscriptions for this user as expired
            await conn.execute(
                """
                UPDATE subscriptions
                SET status = 'expired', updated_at = now()
                WHERE user_id = $1 AND status = 'active'
                """,
                UUID(user_id),
            )
            # 2. Reset user tier
            row = await conn.fetchrow(
                "UPDATE users SET tier = 'seeker' WHERE id = $1 RETURNING id",
                UUID(user_id),
            )
            return bool(row)


async def expire_ended_subscriptions() -> int:
    """Mark active subscriptions whose period has ended as 'expired' and
    downgrade the corresponding users to 'seeker'.  Runs in a single transaction
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
                   SET tier = 'seeker'
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
    # Memory coverage: % of Super Fan users with non-empty memory summary
    memory_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE u.tier = 'super_fan')::int AS super_fan_count,
            COUNT(*) FILTER (WHERE u.tier = 'super_fan' AND m.summary IS NOT NULL AND m.summary != '')::int AS memory_count
        FROM users u
        LEFT JOIN user_memory m ON m.user_id = u.id
        """
    )
    super_fan_count = memory_row["super_fan_count"] or 0
    memory_count = memory_row["memory_count"] or 0
    memory_coverage_pct = round(memory_count / super_fan_count * 100) if super_fan_count > 0 else None
    return {
        "active_users_today": active_users,
        "total_sessions": total_sessions,
        "total_turns": total_turns,
        "avg_latency_ms": round(avg_latency) if avg_latency else None,
        "super_fan_count": super_fan_count,
        "memory_coverage_pct": memory_coverage_pct,
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
            COALESCE(sess.total_sessions,      0)::int    AS total_sessions,
            COALESCE(sess.total_tokens,        0)::bigint AS total_tokens,
            COALESCE(sess.total_audio_seconds, 0)::int    AS total_audio_seconds,
            sess.last_active                               AS last_active,
            sub.current_period_end                         AS subscription_expires
        FROM users u
        LEFT JOIN LATERAL (
            SELECT
                COUNT(DISTINCT s.id)                     AS total_sessions,
                SUM(t.tokens_used)                       AS total_tokens,
                MAX(s.started_at)                        AS last_active,
                COALESCE(SUM(t.audio_played_seconds), 0) AS total_audio_seconds
            FROM sessions s
            LEFT JOIN turns t ON t.session_id = s.id
            WHERE s.user_id = u.id
        ) sess ON true
        LEFT JOIN LATERAL (
            SELECT current_period_end
            FROM subscriptions
            WHERE user_id = u.id AND status = 'active' AND current_period_end > now()
            ORDER BY current_period_end DESC
            LIMIT 1
        ) sub ON true
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


# ── Quota Packs ───────────────────────────────────────────────────────────────

async def is_pack_token_used(purchase_token: str) -> bool:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id FROM quota_packs WHERE purchase_token = $1", purchase_token
    )
    return row is not None


async def credit_quota_pack(
    user_id: str, product_id: str, purchase_token: str, seconds: int
) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO quota_packs (user_id, product_id, purchase_token, seconds_remaining, turns_remaining)
        VALUES ($1, $2, $3, $4, 0)
        """,
        UUID(user_id), product_id, purchase_token, seconds,
    )


async def consume_pack_turn(user_id: str) -> bool:
    """Consume one pack turn for a user (oldest pack first). Returns True if consumed."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id FROM quota_packs
                WHERE user_id = $1 AND turns_remaining > 0
                ORDER BY purchased_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                UUID(user_id),
            )
            if not row:
                return False
            await conn.execute(
                "UPDATE quota_packs SET turns_remaining = turns_remaining - 1 WHERE id = $1",
                row["id"],
            )
    return True


async def get_pack_turns_remaining(user_id: str) -> int:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT COALESCE(SUM(turns_remaining), 0) AS total FROM quota_packs WHERE user_id = $1",
        UUID(user_id),
    )
    return int(row["total"]) if row else 0


async def consume_pack_seconds(user_id: str, amount: int) -> int:
    """Deduct up to `amount` seconds from the oldest pack row with seconds_remaining > 0.
    Returns the actual seconds deducted (0 if pool empty)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, seconds_remaining FROM quota_packs
                WHERE user_id = $1 AND seconds_remaining > 0
                ORDER BY purchased_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                UUID(user_id),
            )
            if not row:
                return 0
            deduct = min(amount, row["seconds_remaining"])
            await conn.execute(
                "UPDATE quota_packs SET seconds_remaining = seconds_remaining - $1 WHERE id = $2",
                deduct, row["id"],
            )
    return deduct


async def get_pack_seconds_remaining(user_id: str) -> int:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT COALESCE(SUM(seconds_remaining), 0) AS total FROM quota_packs WHERE user_id = $1",
        UUID(user_id),
    )
    return int(row["total"]) if row else 0


# ── User Memory ───────────────────────────────────────────────────────────────

async def get_user_memory(user_id: str) -> dict | None:
    pool = get_pool()
    rows = await pool.fetch(
        """SELECT type, phrase FROM user_memory
           WHERE user_id = $1
           ORDER BY type, frequency DESC, confidence DESC""",
        UUID(user_id),
    )
    if not rows:
        return None
    by_type: dict[str, list[str]] = {}
    for row in rows:
        by_type.setdefault(row["type"], []).append(row["phrase"])
    parts = [f"{t.capitalize()}s: {'; '.join(phrases)}" for t, phrases in by_type.items()]
    return {"summary": ". ".join(parts), "key_facts": by_type}


async def upsert_user_memory(user_id: str, summary: str, key_facts: dict) -> None:
    import logging
    logging.getLogger(__name__).info(
        "[queries] upsert_user_memory skipped — phrase-based schema requires embeddings (v2 feature)"
    )


async def delete_user_memory(user_id: str) -> None:
    pool = get_pool()
    await pool.execute("DELETE FROM user_memory WHERE user_id = $1", UUID(user_id))


# ── Waitlist ───────────────────────────────────────────────────────────────────

async def get_waitlist_by_email(email: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id FROM waitlist_signups WHERE LOWER(email) = LOWER($1)",
        email,
    )
    return dict(row) if row else None


async def get_waitlist_by_promo_code(promo_code: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id FROM waitlist_signups WHERE merch_promo_code = $1 AND merch_code_redeemed_at IS NULL",
        promo_code,
    )
    return dict(row) if row else None


async def get_waitlist_by_merch_code_with_email(promo_code: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT email, merch_code_redeemed_at FROM waitlist_signups WHERE merch_promo_code = $1",
        promo_code,
    )
    return dict(row) if row else None


async def redeem_merch_promo_code(promo_code: str) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE waitlist_signups SET merch_code_redeemed_at = NOW() WHERE merch_promo_code = $1",
        promo_code,
    )


# ── Merch Products ─────────────────────────────────────────────────────────────

def compute_effective_price(product: dict) -> dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    start = product.get("presale_start_at")
    end = product.get("presale_end_at")
    pct = product.get("presale_discount_pct")
    price = product["price_inr"]

    if start and end and pct and start <= now <= end:
        discounted = int(price * (1 - pct / 100.0))
        product["is_presale_active"] = True
        product["effective_price_inr"] = discounted
        product["presale_price_inr"] = discounted
        product["presale_ends_at"] = end
    else:
        product["is_presale_active"] = False
        product["effective_price_inr"] = price
        product["presale_price_inr"] = None
        product["presale_ends_at"] = None
    return product


async def list_merch_products(enabled_only: bool = True) -> list[dict]:
    import json as _json
    pool = get_pool()
    query = """
        SELECT
            p.id, p.name, p.description, p.price_inr, p.image_url, p.variants, p.enabled,
            p.presale_start_at, p.presale_end_at, p.presale_discount_pct,
            COUNT(o.id)::int as recent_bought_count
        FROM merch_products p
        LEFT JOIN merch_orders o ON p.id = o.product_id
            AND o.status IN ('paid', 'fulfilled', 'delivered')
            AND o.created_at > NOW() - INTERVAL '30 days'
        WHERE ($1 = false OR p.enabled = true)
        GROUP BY p.id
        ORDER BY p.created_at ASC
    """
    rows = await pool.fetch(query, enabled_only)
    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        if isinstance(d["variants"], str):
            d["variants"] = _json.loads(d["variants"])
        result.append(compute_effective_price(d))
    return result


async def get_merch_product(product_id: str) -> dict | None:
    import json as _json
    pool = get_pool()
    query = """
        SELECT
            p.id, p.name, p.description, p.price_inr, p.image_url, p.variants, p.enabled,
            p.presale_start_at, p.presale_end_at, p.presale_discount_pct,
            COUNT(o.id)::int as recent_bought_count
        FROM merch_products p
        LEFT JOIN merch_orders o ON p.id = o.product_id
            AND o.status IN ('paid', 'fulfilled', 'delivered')
            AND o.created_at > NOW() - INTERVAL '30 days'
        WHERE p.id = $1
        GROUP BY p.id
    """
    row = await pool.fetchrow(query, UUID(product_id))
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    if isinstance(d["variants"], str):
        d["variants"] = _json.loads(d["variants"])
    return compute_effective_price(d)


async def create_merch_product(name: str, description: str | None, price_inr: int, image_url: str | None, variants: list) -> dict:
    import json as _json
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO merch_products (name, description, price_inr, image_url, variants)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        RETURNING id, name, description, price_inr, image_url, variants, enabled
        """,
        name, description, price_inr, image_url, _json.dumps(variants),
    )
    d = dict(row)
    d["id"] = str(d["id"])
    if isinstance(d["variants"], str):
        d["variants"] = _json.loads(d["variants"])
    return d


async def update_merch_product(product_id: str, updates: dict) -> dict | None:
    import json as _json
    pool = get_pool()
    sets = []
    vals = []
    i = 1
    for key, val in updates.items():
        if key == "variants":
            sets.append(f"variants = ${i}::jsonb")
            vals.append(_json.dumps(val))
        else:
            sets.append(f"{key} = ${i}")
            vals.append(val)
        i += 1
    if not sets:
        return await get_merch_product(product_id)
    vals.append(UUID(product_id))
    row = await pool.fetchrow(
        f"UPDATE merch_products SET {', '.join(sets)} WHERE id = ${i} "
        f"RETURNING id, name, description, price_inr, image_url, variants, enabled, "
        f"presale_start_at, presale_end_at, presale_discount_pct",
        *vals,
    )
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    if isinstance(d["variants"], str):
        d["variants"] = _json.loads(d["variants"])
    return compute_effective_price(d)


# ── Merch Orders ──────────────────────────────────────────────────────────────

async def create_merch_order(
    user_id: str, product_id: str, variant_id: str,
    amount_inr: int, original_amount_inr: int, promo_code: str | None,
    razorpay_order_id: str,
) -> dict:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO merch_orders
          (user_id, product_id, variant_id, amount_inr, original_amount_inr, promo_code, razorpay_order_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, user_id, product_id, variant_id, amount_inr, original_amount_inr,
                  promo_code, razorpay_order_id, status
        """,
        UUID(user_id), UUID(product_id), variant_id, amount_inr,
        original_amount_inr, promo_code, razorpay_order_id,
    )
    d = dict(row)
    d["id"] = str(d["id"])
    d["user_id"] = str(d["user_id"])
    d["product_id"] = str(d["product_id"])
    return d


async def get_merch_order(order_id: str) -> dict | None:
    import json as _json
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, user_id, product_id, variant_id, amount_inr, original_amount_inr,
               promo_code, razorpay_order_id, razorpay_payment_id, status,
               shipping_address, shiprocket_order_id, shiprocket_awb, shiprocket_status
        FROM merch_orders WHERE id = $1
        """,
        UUID(order_id),
    )
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    d["user_id"] = str(d["user_id"])
    d["product_id"] = str(d["product_id"])
    if isinstance(d.get("shipping_address"), str):
        d["shipping_address"] = _json.loads(d["shipping_address"])
    return d


async def get_merch_order_by_shiprocket_id(shiprocket_order_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id FROM merch_orders WHERE shiprocket_order_id = $1",
        shiprocket_order_id,
    )
    return dict(row) if row else None


async def confirm_merch_order(order_id: str, razorpay_payment_id: str, shipping_address: dict) -> None:
    import json as _json
    pool = get_pool()
    await pool.execute(
        """
        UPDATE merch_orders
        SET status = 'paid', razorpay_payment_id = $1, shipping_address = $2::jsonb, updated_at = NOW()
        WHERE id = $3
        """,
        razorpay_payment_id, _json.dumps(shipping_address), UUID(order_id),
    )


async def fulfill_merch_order(order_id: str, shiprocket_order_id: str, shiprocket_awb: str) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE merch_orders
        SET status = 'fulfilled', shiprocket_order_id = $1, shiprocket_awb = $2, updated_at = NOW()
        WHERE id = $3
        """,
        shiprocket_order_id, shiprocket_awb, UUID(order_id),
    )


async def update_merch_order_status(order_id: str, status: str, shiprocket_status: str | None = None) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE merch_orders
        SET status = $1, shiprocket_status = $2, updated_at = NOW()
        WHERE id = $3
        """,
        status, shiprocket_status, UUID(order_id),
    )


async def update_merch_order_by_shiprocket_id(shiprocket_order_id: str, status: str, shiprocket_status: str) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE merch_orders
        SET status = $1, shiprocket_status = $2, updated_at = NOW()
        WHERE shiprocket_order_id = $3
        """,
        status, shiprocket_status, shiprocket_order_id,
    )


async def get_user_orders(user_id: str) -> list[dict]:
    import json as _json
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT o.id, o.product_id, o.variant_id, o.amount_inr, o.status,
               o.shiprocket_awb, o.shiprocket_status, o.created_at,
               p.name as product_name,
               (SELECT v->>'label' FROM jsonb_array_elements(p.variants) v WHERE v->>'id' = o.variant_id LIMIT 1) as variant_label
        FROM merch_orders o
        JOIN merch_products p ON o.product_id = p.id
        WHERE o.user_id = $1
        ORDER BY o.created_at DESC
        """,
        UUID(user_id),
    )
    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        d["product_id"] = str(d["product_id"])
        d["created_at"] = d["created_at"].isoformat()
        result.append(d)
    return result


async def auto_confirm_merch_order(order_id: str, shiprocket_order_id: str, shiprocket_awb: str) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE merch_orders
        SET status = 'confirmed', shiprocket_order_id = $1, shiprocket_awb = $2, updated_at = NOW()
        WHERE id = $3
        """,
        shiprocket_order_id, shiprocket_awb, UUID(order_id),
    )


async def list_merch_orders(status: str | None = None) -> list[dict]:
    import json as _json
    pool = get_pool()
    where = "WHERE o.status = $1" if status else ""
    args = [status] if status else []
    rows = await pool.fetch(
        f"""
        SELECT o.id, o.user_id, o.product_id, o.variant_id, o.amount_inr, o.original_amount_inr,
               o.promo_code, o.razorpay_order_id, o.razorpay_payment_id, o.status,
               o.shipping_address, o.shiprocket_order_id, o.shiprocket_awb, o.shiprocket_status,
               o.created_at, u.email as user_email, p.name as product_name
        FROM merch_orders o
        JOIN users u ON o.user_id = u.id
        JOIN merch_products p ON o.product_id = p.id
        {where}
        ORDER BY o.created_at DESC
        """,
        *args,
    )
    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        d["user_id"] = str(d["user_id"])
        d["product_id"] = str(d["product_id"])
        d["created_at"] = d["created_at"].isoformat()
        if isinstance(d.get("shipping_address"), str):
            d["shipping_address"] = _json.loads(d["shipping_address"])
        result.append(d)
    return result


# ── Quiz ──────────────────────────────────────────────────────────────────────

async def get_quiz_questions() -> list:
    import json as _json
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, question_text, options, allows_multiple_select, display_order FROM quiz_questions WHERE enabled = true ORDER BY display_order"
    )
    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d["options"], str):
            d["options"] = _json.loads(d["options"])
        result.append(d)
    return result


async def get_quiz_questions_by_ids(question_ids: list[str]) -> list:
    import json as _json
    pool = get_pool()
    uuids = [UUID(qid) for qid in question_ids]
    rows = await pool.fetch(
        "SELECT id, question_text, options, allows_multiple_select FROM quiz_questions WHERE id = ANY($1)",
        uuids,
    )
    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d["options"], str):
            d["options"] = _json.loads(d["options"])
        result.append(d)
    return result


async def get_user_valid_assessment(user_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, expires_at FROM user_rgv_assessments
        WHERE user_id = $1 AND status = 'ready' AND expires_at > now()
        ORDER BY version DESC LIMIT 1
        """,
        UUID(user_id),
    )
    return dict(row) if row else None


async def get_next_assessment_version(user_id: str) -> int:
    pool = get_pool()
    val = await pool.fetchval(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM user_rgv_assessments WHERE user_id = $1",
        UUID(user_id),
    )
    return val or 1


async def create_assessment(user_id: str, session_id: str, raw_answers: list[dict], version: int) -> str:
    import json as _json
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO user_rgv_assessments (user_id, raw_answers, assessment_text, expires_at, version, status)
        VALUES ($1, $2::jsonb, '', now() + interval '30 days', $3, 'processing')
        RETURNING id
        """,
        UUID(user_id),
        _json.dumps(raw_answers),
        version,
    )
    return str(row["id"])


async def insert_quiz_responses(user_id: str, session_id: str, answers: list) -> None:
    pool = get_pool()
    session_uuid = UUID(session_id)
    user_uuid = UUID(user_id)
    rows = [
        (
            user_uuid,
            session_uuid,
            UUID(str(a.question_id)),
            a.selected_option_indices,
        )
        for a in answers
    ]
    await pool.executemany(
        """
        INSERT INTO user_quiz_responses (user_id, assessment_session_id, question_id, selected_option_indices)
        VALUES ($1, $2, $3, $4)
        """,
        rows,
    )


async def update_assessment_ready(assessment_id: str, assessment_text: str) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE user_rgv_assessments SET assessment_text = $1, status = 'ready' WHERE id = $2",
        assessment_text, UUID(assessment_id),
    )


async def update_assessment_status(assessment_id: str, status: str) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE user_rgv_assessments SET status = $1 WHERE id = $2",
        status, UUID(assessment_id),
    )


async def get_latest_assessment(user_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, status, assessment_text, assessed_at, expires_at, version
        FROM user_rgv_assessments
        WHERE user_id = $1
        ORDER BY version DESC LIMIT 1
        """,
        UUID(user_id),
    )
    return dict(row) if row else None


async def get_user_conversation_count(user_id: str) -> int:
    pool = get_pool()
    val = await pool.fetchval(
        "SELECT COUNT(*) FROM sessions WHERE user_id = $1 AND turn_count > 0",
        UUID(user_id),
    )
    return int(val or 0)
