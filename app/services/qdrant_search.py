import asyncio
import logging
import httpx
from core.config import settings
from services.embedding import embed_query
from services.claude import haiku_call

logger = logging.getLogger(__name__)

SEARCH_LIMIT = 10
RERANK_TOP_K = 5
QUALITY_THRESHOLD = 2.5

_http_client = httpx.AsyncClient(timeout=15.0, limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))


def _qdrant_url() -> str:
    return f"{settings.qdrant_url}/collections/{settings.qdrant_collection}"


async def hybrid_search(query_text: str) -> list[dict]:
    try:
        embedding = await embed_query(query_text)
    except Exception:
        logger.exception("[rag] embedding lookup failed; query len=%d", len(query_text))
        return []

    dense_payload = {
        "vector": {"name": "dense", "vector": embedding["dense"]},
        "filter": {"must": [{"key": "enabled", "match": {"value": True}}]},
        "limit": SEARCH_LIMIT,
        "with_payload": True,
    }
    sparse_payload = {
        "vector": {
            "name": "sparse",
            "vector": {
                "indices": [int(i) for i in embedding["sparse"]["indices"]],
                "values": embedding["sparse"]["values"],
            },
        },
        "filter": {"must": [{"key": "enabled", "match": {"value": True}}]},
        "limit": SEARCH_LIMIT,
        "with_payload": True,
    }

    search_url = f"{_qdrant_url()}/points/search"
    try:
        dense_r, sparse_r = await asyncio.gather(
            _http_client.post(search_url, json=dense_payload),
            _http_client.post(search_url, json=sparse_payload),
        )
        dense_r.raise_for_status()
        sparse_r.raise_for_status()
    except httpx.RequestError as e:
        logger.exception("[rag] qdrant request failed url=%s query len=%d error=%r", search_url, len(query_text), e)
        return []
    except httpx.HTTPStatusError as e:
        logger.exception(
            "[rag] qdrant bad response url=%s status=%s body=%r",
            search_url,
            e.response.status_code,
            e.response.text[:500],
        )
        return []

    dense_results = dense_r.json().get("result", [])
    sparse_results = sparse_r.json().get("result", [])

    return _rrf_fuse(dense_results, sparse_results)


def _rrf_fuse(dense: list, sparse: list, k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    payloads: dict[str, dict] = {}

    for rank, item in enumerate(dense):
        pid = item["id"]
        scores[pid] = scores.get(pid, 0) + 1 / (k + rank + 1)
        payloads[pid] = item["payload"]

    for rank, item in enumerate(sparse):
        pid = item["id"]
        scores[pid] = scores.get(pid, 0) + 1 / (k + rank + 1)
        payloads.setdefault(pid, item["payload"])

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [{"id": pid, "score": score, "payload": payloads[pid]} for pid, score in ranked]


def _score_order(chunks: list[dict]) -> list[dict]:
    """Score-based ordering: 70% RRF + 30% quality_score. Replaces Haiku rerank."""
    max_rrf = max((c["score"] for c in chunks), default=1.0) or 1.0
    def combined(c: dict) -> float:
        rrf = c["score"] / max_rrf
        quality = min(c["payload"].get("quality_score", 3.0), 5.0) / 5.0
        return 0.7 * rrf + 0.3 * quality
    return sorted(chunks, key=combined, reverse=True)[:RERANK_TOP_K]


async def rerank_with_haiku(query: str, chunks: list[dict]) -> list[dict]:
    if not chunks:
        return []

    numbered = "\n\n".join(
        f"[{i+1}] {c['payload'].get('text', '')[:300]}" for i, c in enumerate(chunks)
    )
    prompt = (
        f"Query: {query}\n\nRank these chunks by relevance (most relevant first).\n"
        f"Return only a comma-separated list of numbers e.g. 3,1,2\n\n{numbered}"
    )
    result = ""
    try:
        result = await haiku_call(prompt)
        raw_order = [int(x.strip()) - 1 for x in result.split(",") if x.strip().isdigit()]
        # deduplicate while preserving order
        seen_order: dict[int, None] = dict.fromkeys(i for i in raw_order if i < len(chunks))
        reranked = [chunks[i] for i in seen_order]
        seen = {c["id"] for c in reranked}
        for c in chunks:
            if c["id"] not in seen:
                reranked.append(c)
        return reranked[:RERANK_TOP_K]
    except Exception as e:
        logger.warning("[rerank] parse failure: %r | raw output: %r", e, result)
        return chunks[:RERANK_TOP_K]


async def search_chunks(query: str) -> list[dict]:
    fused = await hybrid_search(query)
    qualified = [c for c in fused if c["payload"].get("quality_score", 3) >= QUALITY_THRESHOLD]
    if not qualified:
        qualified = fused
    candidates = qualified[:SEARCH_LIMIT]
    if settings.rag_rerank_enabled:
        return await rerank_with_haiku(query, candidates)
    return _score_order(candidates)
