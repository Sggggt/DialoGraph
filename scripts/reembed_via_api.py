"""Re-embed all zero-vector chunks via the API container.

This script calls the API server's search endpoint with a test query to verify 
connectivity, then uses httpx to call the embedding API through the model bridge
(running locally), writing results directly to Qdrant.

Usage:
    python scripts/reembed_via_api.py [--batch-size 5]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

API = "http://127.0.0.1:8000/api"
QDRANT = "http://127.0.0.1:6333"


def get_courses() -> list[dict]:
    r = httpx.get(f"{API}/courses", timeout=10)
    r.raise_for_status()
    return r.json()


def get_settings() -> dict:
    r = httpx.get(f"{API}/settings/model", timeout=10)
    r.raise_for_status()
    return r.json()


def scroll_zero_vectors(limit: int = 100, offset=None) -> tuple[list[dict], str | None]:
    body: dict = {"limit": limit, "with_vector": True, "with_payload": True}
    if offset is not None:
        body["offset"] = offset
    r = httpx.post(f"{QDRANT}/collections/knowledge_chunks/points/scroll", json=body, timeout=30)
    r.raise_for_status()
    data = r.json()["result"]
    zeros = []
    for pt in data["points"]:
        if all(abs(v) < 1e-12 for v in pt["vector"]):
            zeros.append(pt)
    return zeros, data.get("next_page_offset")


def call_embeddings_via_api(texts: list[str], settings: dict) -> list[list[float]]:
    """Call the embedding endpoint directly through the running API container."""
    # We'll use docker exec to call the embedding from inside the API container
    import subprocess
    import tempfile
    import os

    script = f"""
import asyncio, json, sys
sys.path.insert(0, '/app')
from app.services.embeddings import EmbeddingProvider
async def main():
    texts = json.loads(sys.stdin.read())
    embedder = EmbeddingProvider()
    result = await embedder.embed_texts_with_meta(texts, text_type="document")
    json.dump(result.vectors, sys.stdout)
asyncio.run(main())
"""
    proc = subprocess.run(
        ["docker", "exec", "-i", "course-kg-api", "python", "-c", script],
        input=json.dumps(texts),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker exec failed: {proc.stderr}")
    return json.loads(proc.stdout)


def upsert_qdrant(points: list[dict]) -> None:
    body = {"points": [{"id": p["id"], "vector": p["vector"], "payload": p["payload"]} for p in points]}
    r = httpx.put(f"{QDRANT}/collections/knowledge_chunks/points", json=body, timeout=30)
    r.raise_for_status()


def build_embedding_text(payload: dict) -> str:
    """Reconstruct the embedding text from the Qdrant payload."""
    doc_title = payload.get("document_title", "")
    chapter = payload.get("chapter", "")
    section = payload.get("section", "")
    source_type = payload.get("source_type", "")
    content_kind = payload.get("content_kind", "")
    content = payload.get("content", "")

    # Match the embedding_text function in chunking.py
    parts = []
    if doc_title:
        parts.append(f"document: {doc_title}")
    if chapter:
        parts.append(f"chapter: {chapter}")
    if section:
        parts.append(f"section: {section}")
    if source_type:
        parts.append(f"type: {source_type}")
    if content_kind and content_kind != "text":
        parts.append(f"kind: {content_kind}")
    header = " | ".join(parts)
    if header:
        return f"{header}\n\n{content}"
    return content


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    print(f"Model: {settings['embedding_model']}, Dimensions: {settings['embedding_dimensions']}")
    print(f"Has API key: {settings['has_api_key']}, Degraded: {settings['degraded_mode']}")
    print()

    # Collect all zero-vector points
    print("Scanning Qdrant for zero vectors...")
    all_zeros: list[dict] = []
    offset = None
    while True:
        zeros, offset = scroll_zero_vectors(100, offset)
        all_zeros.extend(zeros)
        if offset is None:
            break
    print(f"Found {len(all_zeros)} zero-vector points")

    if not all_zeros or args.dry_run:
        return

    # Process in batches
    fixed = 0
    errors = 0
    for i in range(0, len(all_zeros), args.batch_size):
        batch = all_zeros[i : i + args.batch_size]
        texts = [build_embedding_text(pt["payload"]) for pt in batch]

        try:
            vectors = call_embeddings_via_api(texts, settings)
        except Exception as e:
            print(f"  [ERROR] Batch {i}: {e}")
            errors += len(batch)
            continue

        # Validate
        valid = True
        for j, vec in enumerate(vectors):
            if len(vec) != settings["embedding_dimensions"]:
                print(f"  [ERROR] Vector {j} has wrong dim: {len(vec)}")
                valid = False
            if all(abs(v) < 1e-12 for v in vec):
                print(f"  [ERROR] Vector {j} is still zero!")
                valid = False
        if not valid:
            errors += len(batch)
            continue

        # Update points with real vectors
        updated = []
        for pt, vec in zip(batch, vectors):
            updated.append({"id": pt["id"], "vector": vec, "payload": pt["payload"]})
        upsert_qdrant(updated)
        fixed += len(batch)
        pct = (i + len(batch)) / len(all_zeros) * 100
        print(f"  [{i + len(batch)}/{len(all_zeros)}] ({pct:.0f}%) OK")

    print(f"\n=== Done ===")
    print(f"Fixed: {fixed}, Errors: {errors}, Total: {len(all_zeros)}")


if __name__ == "__main__":
    main()
