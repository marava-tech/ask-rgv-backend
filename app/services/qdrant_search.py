import asyncio
import logging
import httpx
from core.config import settings
from services.embedding import embed_query
from services.claude import haiku_call

logger = logging.getLogger(__name__)

QDRANT_SEARCH_URL = None
SEARCH_LIMIT = 10
RERANK_TOP_K = 5
QUALITY_THRESHOLD = 2.5


def _qdrant_url() -> str:
    return f"{settings.qdrant_url}/collections/{settings.qdrant_collection}"


async def hybrid_search(query_text: str) -> list[dict]:
    embedding = await embed_query(query_text)

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
                "indices": embedding["sparse"]["indices"],
                "values": embedding["sparse"]["values"],
            },
        },
        "filter": {"must": [{"key": "enabled", "match": {"value": True}}]},
        "limit": SEARCH_LIMIT,
        "with_payload": True,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        dense_r, sparse_r = await asyncio.gather(
            client.post(f"{_qdrant_url()}/points/search", json=dense_payload),
            client.post(f"{_qdrant_url()}/points/search", json=sparse_payload),
        )
        dense_r.raise_for_status()
        sparse_r.raise_for_status()

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
    try:
        result = await haiku_call(prompt)
        order = [int(x.strip()) - 1 for x in result.split(",") if x.strip().isdigit()]
        reranked = [chunks[i] for i in order if i < len(chunks)]
        seen = {c["id"] for c in reranked}
        for c in chunks:
            if c["id"] not in seen:
                reranked.append(c)
        return reranked[:RERANK_TOP_K]
    except Exception as e:
        # Bug #25: parse failure was silently swallowed with no log — now observable
        logger.warning("[rerank] parse failure: %r | raw output: %r", e, result if 'result' in dir() else "<no result>")
        return chunks[:RERANK_TOP_K]


async def search_chunks(query: str) -> list[dict]:
    fused = await hybrid_search(query)
    qualified = [c for c in fused if c["payload"].get("quality_score", 3) >= QUALITY_THRESHOLD]
    if not qualified:
        qualified = fused
    reranked = await rerank_with_haiku(query, qualified[:SEARCH_LIMIT])
    return reranked
