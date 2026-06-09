#!/usr/bin/env python3
"""Rate-limited batch importer for OpenViking.
Uploads complete Markdown batches (bug details + comments + attachment summaries).

Usage:
    python3 import_to_openviking_v2.py

Configuration (edit variables below or use defaults):
    BATCH_DIR   - Directory containing batch_*.md files
    BASE_URL    - OpenViking API address
    AUTH        - API authentication headers
    REQUEST_INTERVAL - Seconds between batch uploads (default: 3)
"""
import json, os, sys, time, requests
from datetime import datetime

BATCH_DIR = os.path.expanduser("~/.openviking/workspace/feishu-bugs/import_batches/")
BASE_URL = "http://127.0.0.1:1934"

HEADERS_AUTH = {
    "Authorization": "Bearer <OV_API_KEY>",
    "X-OpenViking-Account": "default",
    "X-OpenViking-User": "admin",
}
API_HEADERS = {**HEADERS_AUTH, "Content-Type": "application/json"}

REQUEST_INTERVAL = 3
MAX_RETRIES = 3

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def check_health():
    try:
        r = requests.get(f"{BASE_URL}/api/v1/health", timeout=5)
        return r.status_code
    except Exception:
        return None

def upload_temp(filepath):
    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{BASE_URL}/api/v1/resources/temp_upload",
            headers=HEADERS_AUTH,
            files={"file": (filename, f, "text/markdown")},
            timeout=60,
        )
    resp.raise_for_status()
    return resp.json()["result"]["temp_file_id"]

def add_resource(temp_file_id, filename):
    resp = requests.post(
        f"{BASE_URL}/api/v1/resources",
        headers=API_HEADERS,
        json={
            "temp_file_id": temp_file_id,
            "reason": f"Feishu bug complete import: {filename}",
            "instruction": "Extract all bug details including title, status, type, template, module, creation time, assignee, reporter, project, comments, and attachment summaries for semantic search. Each ## heading is a separate bug.",
            "wait": False,
            "telemetry": False,
            "strict": True,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

def main():
    log("=" * 60)
    log("Starting batch import to OpenViking")
    log(f"Source: {BATCH_DIR}")

    status = check_health()
    log(f"Service status: {status}")
    if status is None:
        log("ERROR: OpenViking service not reachable!")
        sys.exit(1)

    batch_files = sorted([
        f for f in os.listdir(BATCH_DIR)
        if f.startswith("batch_") and f.endswith(".md")
    ])
    log(f"Batches to process: {len(batch_files)}")
    log(f"Rate limit: {REQUEST_INTERVAL}s between batches")
    log("=" * 60)

    succeeded = 0
    failed = []

    for i, filename in enumerate(batch_files, 1):
        filepath = os.path.join(BATCH_DIR, filename)
        file_size = os.path.getsize(filepath)

        temp_id = None
        for retry in range(MAX_RETRIES):
            try:
                temp_id = upload_temp(filepath)
                break
            except Exception as e:
                if retry < MAX_RETRIES - 1:
                    log(f"  [{i}/{len(batch_files)}] Upload retry {retry+1}: {e}")
                    time.sleep(2)
                else:
                    log(f"  [{i}/{len(batch_files)}] FAIL upload: {filename} - {e}")
                    failed.append(filename)
                    continue

        if not temp_id:
            continue

        for retry in range(MAX_RETRIES):
            try:
                result = add_resource(temp_id, filename)
                root_uri = result.get("result", {}).get("root_uri", "unknown")
                log(f"  [{i}/{len(batch_files)}] OK: {filename} ({file_size/1024:.1f}KB) -> {root_uri}")
                succeeded += 1
                break
            except Exception as e:
                if retry < MAX_RETRIES - 1:
                    log(f"  [{i}/{len(batch_files)}] Register retry {retry+1}: {e}")
                    time.sleep(2)
                else:
                    log(f"  [{i}/{len(batch_files)}] FAIL register: {filename} - {e}")
                    failed.append(filename)

        if i < len(batch_files):
            time.sleep(REQUEST_INTERVAL)

    log("=" * 60)
    log(f"Import complete: {succeeded}/{len(batch_files)} batches registered")
    if failed:
        log(f"Failed batches ({len(failed)}):")
        for f in failed[:10]:
            log(f"  - {f}")
        if len(failed) > 10:
            log(f"  ... and {len(failed)-10} more")

if __name__ == "__main__":
    main()
