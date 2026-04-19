import uuid
import httpx


async def upsert_chunks(
    chunks: list[str],
    embeddings: list[dict],
    scores: list[float],
    video_id: str,
    source_title: str,
    source_url: str,
    source_language: str,
    qdrant_url: str,
    collection: str,
) -> None:
    points = []
    for i, (chunk, emb, score) in enumerate(zip(chunks, embeddings, scores)):
        points.append({
            "id": str(uuid.uuid4()),
            "vector": {
                "dense": emb["dense"],
                "sparse": {
                    "indices": [int(i) for i in emb["sparse"]["indices"]],
                    "values": emb["sparse"]["values"],
                },
            },
            "payload": {
                "text": chunk,
                "video_id": video_id,
                "source_title": source_title,
                "source_url": source_url,
                "source_language": source_language,
                "language": "en",
                "quality_score": score,
                "chunk_index": i,
                "enabled": True,
            },
        })

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.put(
            f"{qdrant_url}/collections/{collection}/points",
            json={"points": points},
        )
        response.raise_for_status()


async def toggle_chunks(video_id: str, enabled: bool, settings) -> None:
    payload = {
        "payload": {"enabled": enabled},
        "filter": {"must": [{"key": "video_id", "match": {"value": video_id}}]},
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points/payload",
            json=payload,
        )
        response.raise_for_status()


async def ensure_collection(qdrant_url: str, collection: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{qdrant_url}/collections/{collection}")
        if r.status_code == 200:
            return
        payload = {
            "vectors": {"dense": {"size": 1024, "distance": "Cosine"}},
            "sparse_vectors": {"sparse": {}},
        }
        r = await client.put(f"{qdrant_url}/collections/{collection}", json=payload)
        r.raise_for_status()
