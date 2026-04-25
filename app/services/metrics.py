import json
import logging
import time

# Dedicated logger — in Docker, filter with: docker logs <container> | grep turn_complete
_log = logging.getLogger("ask_rgv.metrics")


def log_turn(
    *,
    source: str,        # "ws" (voice_stream WebSocket) or "sse" (conversation/turn)
    session_id: str,
    user_id: str | None,
    tier: str,
    lang: str,
    ttft_ms: int | None,
    first_audio_ms: int | None,
    latency_ms: int,
    rag_chunks: int,
    turn_seconds: int,
) -> None:
    """
    Emit one structured JSON line per completed turn.

    Grep pattern (Docker):  docker logs <container> 2>&1 | grep turn_complete
    Parse in shell:         ... | python3 -c "import sys,json; [print(json.loads(l)) for l in sys.stdin]"
    """
    _log.info(json.dumps({
        "event": "turn_complete",
        "ts_ms": int(time.time() * 1000),
        "source": source,
        "session_id": session_id,
        "user_id": user_id,
        "tier": tier,
        "lang": lang,
        "ttft_ms": ttft_ms,
        "first_audio_ms": first_audio_ms,
        "latency_ms": latency_ms,
        "rag_chunks": rag_chunks,
        "turn_seconds": turn_seconds,
    }))
