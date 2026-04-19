import httpx


async def embed_chunks(chunks: list[str], embedding_url: str, batch_size: int = 16) -> list[dict]:
    # BGE-M3 runs on CPU; large batches can take 5-10 min. Use 900s to be safe.
    async with httpx.AsyncClient(timeout=900.0) as client:
        response = await client.post(
            f"{embedding_url}/embed",
            json={"texts": chunks, "batch_size": batch_size},
        )
        response.raise_for_status()
    data = response.json()
    return [
        {"dense": data["dense"][i], "sparse": data["sparse"][i]}
        for i in range(len(chunks))
    ]
