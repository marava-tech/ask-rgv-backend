#!/usr/bin/env python3
"""
One-time seed script: reads the 46 existing transcript .txt files,
extracts YouTube URLs from file headers, and queues each for ingestion.

Usage:
  python scripts/seed_existing.py \
    --dir "garbage-resources/data for RAG/yt-transcripts/" \
    --api-url http://localhost:8000 \
    --admin-password <password>

All 46 source videos are English.
"""
import argparse
import re
import sys
import time
from pathlib import Path

import httpx

YT_URL_PATTERN = re.compile(
    r"(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w\-?=&]+)"
)


def extract_url_from_file(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        print(f"  [warn] cannot read {path.name}: {e}")
        return None
    for line in text.splitlines()[:20]:
        m = YT_URL_PATTERN.search(line)
        if m:
            return m.group(1)
    return None


def get_admin_token(api_url: str, password: str, client: httpx.Client) -> str:
    r = client.post(f"{api_url}/admin/auth/login", json={"password": password}, timeout=10)
    r.raise_for_status()
    return r.json()["admin_token"]


def seed(transcript_dir: str, api_url: str, admin_password: str, dry_run: bool) -> None:
    directory = Path(transcript_dir)
    if not directory.exists():
        print(f"Directory not found: {directory}")
        sys.exit(1)

    txt_files = sorted(directory.glob("*.txt"))
    if not txt_files:
        print(f"No .txt files found in {directory}")
        sys.exit(1)

    print(f"Found {len(txt_files)} .txt files in {directory}")

    urls: list[tuple[str, str]] = []
    for f in txt_files:
        url = extract_url_from_file(f)
        if url:
            urls.append((f.name, url))
        else:
            print(f"  [skip] {f.name} — no YouTube URL found in first 20 lines")

    print(f"\n{len(urls)} URLs extracted. Queuing ingestion...\n")

    if dry_run:
        for name, url in urls:
            print(f"  [dry-run] {name} → {url}")
        return

    with httpx.Client(timeout=30.0) as client:
        token = get_admin_token(api_url, admin_password, client)
        headers = {"Authorization": f"Bearer {token}"}

        ok = 0
        failed = 0
        for name, url in urls:
            try:
                r = client.post(
                    f"{api_url}/admin/ingestion/single",
                    json={"url": url, "language": "en"},
                    headers=headers,
                    timeout=15,
                )
                r.raise_for_status()
                job_id = r.json().get("job_id", "?")
                print(f"  [queued] {name} → job {job_id}")
                ok += 1
            except httpx.HTTPStatusError as e:
                print(f"  [error] {name}: HTTP {e.response.status_code} — {e.response.text[:100]}")
                failed += 1
            except Exception as e:
                print(f"  [error] {name}: {e}")
                failed += 1
            time.sleep(0.2)

    print(f"\nDone — {ok} queued, {failed} failed.")
    if failed:
        print("Re-run to retry failed URLs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed 46 existing RGV transcripts")
    parser.add_argument("--dir", required=True, help="Path to yt-transcripts/ directory")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--admin-password", default="", help="Admin password (or set ADMIN_PASSWORD env)")
    parser.add_argument("--dry-run", action="store_true", help="Print URLs without queuing")
    args = parser.parse_args()

    import os
    password = args.admin_password or os.getenv("ADMIN_PASSWORD", "")
    if not password and not args.dry_run:
        print("Error: --admin-password or ADMIN_PASSWORD env required")
        sys.exit(1)

    seed(args.dir, args.api_url, password, args.dry_run)
