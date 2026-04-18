#!/usr/bin/env python3
"""
Run once after Qdrant is healthy to create the rgv_knowledge collection.
Usage: python scripts/setup_qdrant.py [--qdrant-url http://localhost:6333]
"""
import argparse
import httpx

COLLECTION = "rgv_knowledge"


def create_collection(qdrant_url: str) -> None:
    url = f"{qdrant_url}/collections/{COLLECTION}"
    with httpx.Client(timeout=15.0) as client:
        r = client.get(url)
        if r.status_code == 200:
            print(f"Collection '{COLLECTION}' already exists.")
            return

        payload = {
            "vectors": {"dense": {"size": 1024, "distance": "Cosine"}},
            "sparse_vectors": {"sparse": {}},
        }
        r = client.put(url, json=payload)
        r.raise_for_status()
        print(f"Collection '{COLLECTION}' created successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    args = parser.parse_args()
    create_collection(args.qdrant_url)
