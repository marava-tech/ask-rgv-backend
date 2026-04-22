import logging

import httpx
from core.config import settings

logger = logging.getLogger(__name__)


async def embed_texts(texts: list[str], batch_size: int = 16) -> list[dict]:
    url = f"{settings.embedding_service_url}/embed"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json={"texts": texts, "batch_size": batch_size},
            )
            response.raise_for_status()
        data = response.json()
        return [
            {"dense": data["dense"][i], "sparse": data["sparse"][i]}
            for i in range(len(texts))
        ]
    except httpx.RequestError as e:
        logger.exception("[embed] request failed url=%s error=%r", url, e)
        raise
    except httpx.HTTPStatusError as e:
        logger.exception(
            "[embed] bad response url=%s status=%s body=%r",
            url,
            e.response.status_code,
            e.response.text[:500],
        )
        raise


async def embed_query(text: str) -> dict:
    results = await embed_texts([text])
    return results[0]
