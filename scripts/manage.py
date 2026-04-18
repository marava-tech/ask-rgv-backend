#!/usr/bin/env python3
"""
CLI management tool for Ask RGV.
Usage:
  python scripts/manage.py status
  python scripts/manage.py search "RGV about fear"
  python scripts/manage.py stats
  python scripts/manage.py style-preview --language en
"""
import argparse
import json
import os
import sys
import httpx

API_URL = os.getenv("ASKRGV_API_URL", "http://localhost:8000")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.getenv("QDRANT_COLLECTION", "rgv_knowledge")
EMBED_URL = os.getenv("EMBEDDING_SERVICE_URL", "http://localhost:8001")


def cmd_status():
    with httpx.Client(timeout=10.0) as client:
        rows = client.get(f"{QDRANT_URL}/collections/{COLLECTION}/points/count").json()
        print(f"Qdrant points: {rows.get('result', {}).get('count', '?')}")
        r = client.get(f"{API_URL}/health")
        print(f"API health: {json.dumps(r.json(), indent=2)}")


def cmd_search(query: str):
    with httpx.Client(timeout=30.0) as client:
        emb_r = client.post(f"{EMBED_URL}/embed", json={"texts": [query]})
        emb_r.raise_for_status()
        emb = emb_r.json()

        search_payload = {
            "vector": {"name": "dense", "vector": emb["dense"][0]},
            "filter": {"must": [{"key": "enabled", "match": {"value": True}}]},
            "limit": 5,
            "with_payload": True,
        }
        r = client.post(f"{QDRANT_URL}/collections/{COLLECTION}/points/search", json=search_payload)
        r.raise_for_status()
        results = r.json().get("result", [])

    print(f"\nTop {len(results)} results for: '{query}'\n")
    for i, item in enumerate(results, 1):
        p = item["payload"]
        print(f"[{i}] score={item['score']:.3f}  video={p.get('video_id')}  quality={p.get('quality_score')}")
        print(f"    {p.get('text', '')[:200]}")
        print()


def cmd_stats():
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{QDRANT_URL}/collections/{COLLECTION}")
        r.raise_for_status()
        info = r.json().get("result", {})
        print(json.dumps(info, indent=2))


def cmd_style_preview(language: str):
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../backend"))
    os.environ.setdefault("DATABASE_URL", "postgresql://askrgv:x@localhost:5432/askrgv")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("QDRANT_URL", QDRANT_URL)
    os.environ.setdefault("QDRANT_COLLECTION", COLLECTION)
    os.environ.setdefault("EMBEDDING_SERVICE_URL", EMBED_URL)
    os.environ.setdefault("INGESTION_WORKER_URL", "http://localhost:8002")
    os.environ.setdefault("GOOGLE_CLIENT_ID", "x")
    os.environ.setdefault("JWT_SECRET", "x")
    os.environ.setdefault("ADMIN_JWT_SECRET", "x")
    os.environ.setdefault("ADMIN_PASSWORD", "x")
    os.environ.setdefault("ANTHROPIC_API_KEY", "x")
    import asyncio
    from db.pool import init_pool
    from db.queries import get_style_profiles

    async def run():
        await init_pool()
        quotes = await get_style_profiles(language, limit=20)
        if not quotes:
            print(f"No style profiles for language '{language}'")
            return
        print(f"[STYLE_ANCHORS] — {language}")
        for q in quotes:
            print(f"  - {q}")
    asyncio.run(run())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ask RGV management CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sub.add_parser("stats")
    sp2 = sub.add_parser("style-preview")
    sp2.add_argument("--language", default="en", choices=["en", "te", "hi"])

    args = parser.parse_args()
    if args.command == "status":
        cmd_status()
    elif args.command == "search":
        cmd_search(args.query)
    elif args.command == "stats":
        cmd_stats()
    elif args.command == "style-preview":
        cmd_style_preview(args.language)
    else:
        parser.print_help()
