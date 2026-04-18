import httpx
from core.config import settings


async def embed_texts(texts: list[str], batch_size: int = 16) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.embedding_service_url}/embed",
            json={"texts": texts, "batch_size": batch_size},
        )
        response.raise_for_status()
    data = response.json()
    return [
        {"dense": data["dense"][i], "sparse": data["sparse"][i]}
        for i in range(len(texts))
    ]


async def embed_query(text: str) -> dict:
    results = await embed_texts([text])
    return results[0]
