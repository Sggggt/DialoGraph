"""Re-embed all zero-vector chunks via the model bridge.

Usage:
    python scripts/reembed_via_bridge.py [--batch-size 5] [--dry-run]

This script calls the local model bridge (127.0.0.1:8765) which handles
TLS/SSL for the Dashscope API, and writes corrected vectors to Qdrant.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

QDRANT = "http://127.0.0.1:6333"
BRIDGE = "http://127.0.0.1:8765"
COLLECTION = "knowledge_chunks"


def load_env() -> dict[str, str]:
    env = {}
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip()
    return env


def scroll_all_zero_vectors() -> list[dict]:
    all_zeros = []
    offset = None
    while True:
        body: dict = {"limit": 100, "with_vector": True, "with_payload": True}
        if offset is not None:
            body["offset"] = offset
        r = httpx.post(f"{QDRANT}/collections/{COLLECTION}/points/scroll", json=body, timeout=30)
        r.raise_for_status()
        data = r.json()["result"]
        for pt in data["points"]:
            if all(abs(v) < 1e-12 for v in pt["vector"]):
                all_zeros.append(pt)
        offset = data.get("next_page_offset")
        if offset is None:
            break
    return all_zeros


def build_embedding_text(payload: dict) -> str:
    doc_title = payload.get("document_title", "")
    chapter = payload.get("chapter", "")
    section = payload.get("section", "")
    source_type = payload.get("source_type", "")
    content_kind = payload.get("content_kind", "")
    content = payload.get("content", "")
    parts = []
    if doc_title:
        parts.append(f"document: {doc_title}")
    if chapter:
        parts.append(f"chapter: {chapter}")
    if section:
        parts.append(f"section: {section}")
    if source_type:
        parts.append(f"type: {source_type}")
    if content_kind and content_kind not in ("text", "markdown"):
        parts.append(f"kind: {content_kind}")
    header = " | ".join(parts)
    return f"{header}\n\n{content}" if header else content


def call_embedding_via_bridge(
    texts: list[str],
    api_key: str,
    model: str,
    dimensions: int,
    timeout: float = 120.0,
) -> list[list[float]]:
    """Call the embedding API through the local model bridge (HTTP, no TLS)."""
    payload = {
        "model": model,
        "input": texts,
        "encoding_format": "float",
        "dimensions": dimensions,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # Bridge prepends its target_base_url, so we only need the path
    url = f"{BRIDGE}/embeddings"
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    if "error" in data:
        raise RuntimeError(f"API error: {json.dumps(data['error'], ensure_ascii=False)}")
    return [item["embedding"] for item in data["data"]]


def upsert_qdrant(points: list[dict]) -> None:
    body = {"points": [{"id": p["id"], "vector": p["vector"], "payload": p["payload"]} for p in points]}
    r = httpx.put(f"{QDRANT}/collections/{COLLECTION}/points", json=body, timeout=30)
    r.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    env = load_env()
    api_key = env.get("OPENAI_API_KEY", "")
    model = env.get("EMBEDDING_MODEL", "text-embedding-v4")
    dimensions = int(env.get("EMBEDDING_DIMENSIONS", "1024"))

    if not api_key:
        print("ERROR: OPENAI_API_KEY not found in .env")
        sys.exit(1)

    # Verify bridge is running
    print("Checking model bridge health...")
    try:
        r = httpx.get(f"{BRIDGE}/health", timeout=5)
        health = r.json()
        print(f"  Bridge OK -> {health.get('target_base_url')}")
    except Exception as e:
        print(f"  Bridge NOT available: {e}")
        print("  Make sure the model bridge is running (start-app.ps1)")
        sys.exit(1)

    print(f"Model: {model}, Dimensions: {dimensions}")
    print()

    # Connectivity test
    print("Testing embedding via bridge...")
    try:
        test_vecs = call_embedding_via_bridge(["hello world"], api_key, model, dimensions)
        assert len(test_vecs) == 1 and len(test_vecs[0]) == dimensions
        norm = sum(v * v for v in test_vecs[0]) ** 0.5
        print(f"  OK - {dimensions}-dim vector, norm={norm:.4f}")
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)
    print()

    # Scan for zero vectors
    print("Scanning Qdrant for zero vectors...")
    zeros = scroll_all_zero_vectors()
    print(f"Found {len(zeros)} zero-vector points")

    if args.dry_run or not zeros:
        if args.dry_run:
            print("Dry run — no changes made.")
        return

    fixed = 0
    errors = 0
    for i in range(0, len(zeros), args.batch_size):
        batch = zeros[i : i + args.batch_size]
        texts = [build_embedding_text(pt["payload"]) for pt in batch]

        try:
            vectors = call_embedding_via_bridge(texts, api_key, model, dimensions)
        except Exception as e:
            print(f"  [ERROR] Batch {i}: {e}")
            errors += len(batch)
            continue

        # Validate
        valid = True
        for j, vec in enumerate(vectors):
            if len(vec) != dimensions:
                print(f"  [ERROR] Vector {j} wrong dim: {len(vec)}")
                valid = False
            if all(abs(v) < 1e-12 for v in vec):
                print(f"  [ERROR] Vector {j} still zero!")
                valid = False
        if not valid:
            errors += len(batch)
            continue

        updated = [{"id": pt["id"], "vector": vec, "payload": pt["payload"]} for pt, vec in zip(batch, vectors)]
        upsert_qdrant(updated)
        fixed += len(batch)
        pct = (i + len(batch)) / len(zeros) * 100
        print(f"  [{i + len(batch)}/{len(zeros)}] ({pct:.0f}%) re-embedded")

    print(f"\n=== Done ===")
    print(f"Fixed: {fixed}, Errors: {errors}, Total: {len(zeros)}")


if __name__ == "__main__":
    main()
