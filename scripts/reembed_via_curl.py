"""Re-embed all zero-vector chunks by calling the Dashscope API directly via curl.

Usage:
    python scripts/reembed_via_curl.py [--batch-size 5] [--dry-run]

This script:
1. Scans Qdrant for zero-vector points
2. Calls the Dashscope embedding API via curl (bypassing Python networking issues)
3. Upserts corrected vectors back to Qdrant via HTTP
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import httpx

QDRANT = "http://127.0.0.1:6333"
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


def call_embedding_via_curl(
    texts: list[str],
    api_key: str,
    base_url: str,
    model: str,
    dimensions: int,
) -> list[list[float]]:
    """Call embedding API via curl.exe to avoid Python TLS/proxy issues."""
    payload = {
        "model": model,
        "input": texts,
        "encoding_format": "float",
        "dimensions": dimensions,
    }
    url = f"{base_url.rstrip('/')}/embeddings"

    payload_path = None
    output_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".json") as f:
            json.dump(payload, f, ensure_ascii=False)
            payload_path = f.name
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            output_path = f.name

        curl_bin = shutil.which("curl.exe") or shutil.which("curl")
        if not curl_bin:
            raise RuntimeError("curl not found")

        cmd = [
            curl_bin, "-sS",
            "-X", "POST", url,
            "-H", f"Authorization: Bearer {api_key}",
            "-H", "Content-Type: application/json",
            "-d", f"@{payload_path}",
            "-o", output_path,
            "--max-time", "90",
            "--connect-timeout", "15",
            "-w", "%{http_code}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=100)
        if result.returncode != 0:
            raise RuntimeError(f"curl error: {result.stderr}")

        status = result.stdout.strip()[-3:]
        with open(output_path, "r", encoding="utf-8") as f:
            response = json.load(f)

        if status != "200":
            raise RuntimeError(f"API returned {status}: {json.dumps(response, ensure_ascii=False)[:500]}")

        if "error" in response:
            raise RuntimeError(f"API error: {json.dumps(response['error'], ensure_ascii=False)}")

        return [item["embedding"] for item in response["data"]]
    finally:
        for p in (payload_path, output_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


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
    base_url = env.get("OPENAI_BASE_URL", "")
    model = env.get("EMBEDDING_MODEL", "text-embedding-v4")
    dimensions = int(env.get("EMBEDDING_DIMENSIONS", "1024"))

    if not api_key:
        print("ERROR: OPENAI_API_KEY not found in .env")
        sys.exit(1)

    print(f"Base URL: {base_url}")
    print(f"Model: {model}, Dimensions: {dimensions}")
    print()

    # Quick connectivity test
    print("Testing embedding API connectivity...")
    try:
        test_vecs = call_embedding_via_curl(["test"], api_key, base_url, model, dimensions)
        assert len(test_vecs) == 1 and len(test_vecs[0]) == dimensions
        print(f"  OK - got {dimensions}-dim vector")
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)
    print()

    # Scan for zero vectors
    print("Scanning Qdrant for zero vectors...")
    zeros = scroll_all_zero_vectors()
    print(f"Found {len(zeros)} zero-vector points out of total collection")

    if args.dry_run or not zeros:
        return

    fixed = 0
    errors = 0
    for i in range(0, len(zeros), args.batch_size):
        batch = zeros[i : i + args.batch_size]
        texts = [build_embedding_text(pt["payload"]) for pt in batch]

        try:
            vectors = call_embedding_via_curl(texts, api_key, base_url, model, dimensions)
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

        # Upsert with real vectors
        updated = [{"id": pt["id"], "vector": vec, "payload": pt["payload"]} for pt, vec in zip(batch, vectors)]
        upsert_qdrant(updated)
        fixed += len(batch)
        pct = (i + len(batch)) / len(zeros) * 100
        print(f"  [{i + len(batch)}/{len(zeros)}] ({pct:.0f}%) re-embedded")

    print(f"\n=== Done ===")
    print(f"Fixed: {fixed}, Errors: {errors}, Total: {len(zeros)}")


if __name__ == "__main__":
    main()
