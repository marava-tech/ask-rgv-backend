from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from FlagEmbedding import BGEM3FlagModel
import uvicorn

app = FastAPI(title="BGE-M3 Embedding Service")

model: BGEM3FlagModel | None = None


@app.on_event("startup")
async def load_model():
    global model
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)


class EmbedRequest(BaseModel):
    texts: list[str]
    batch_size: int = 16


@app.post("/embed")
async def embed(request: EmbedRequest):
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    results = model.encode(
        request.texts,
        batch_size=request.batch_size,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    return {
        "dense": results["dense_vecs"].tolist(),
        "sparse": [
            {"indices": list(s.keys()), "values": list(s.values())}
            for s in results["lexical_weights"]
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": model is not None}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
