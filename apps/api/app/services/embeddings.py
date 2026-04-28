from __future__ import annotations

import hashlib
import asyncio
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings


class FallbackDisabledError(RuntimeError):
    pass


def is_degraded_mode() -> bool:
    settings = get_settings()
    return not settings.openai_api_key


class EmbeddingProvider:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def embed_texts(self, texts: list[str], text_type: str = "document") -> list[list[float]]:
        return (await self.embed_texts_with_meta(texts, text_type=text_type)).vectors

    async def embed_texts_with_meta(self, texts: list[str], text_type: str = "document") -> "EmbeddingCallResult":
        if not texts:
            return EmbeddingCallResult(vectors=[], provider="none", external_called=False, fallback_reason=None)
        if not self.settings.openai_api_key:
            if not self.settings.enable_model_fallback:
                raise FallbackDisabledError("OPENAI_API_KEY is required because ENABLE_MODEL_FALLBACK is false")
            return EmbeddingCallResult(
                vectors=[self._fake_embedding(text) for text in texts],
                provider="fake",
                external_called=False,
                fallback_reason="missing_openai_api_key",
            )
        try:
            vectors = await self._openai_compatible_embeddings(texts, text_type=text_type)
            return EmbeddingCallResult(vectors=vectors, provider="openai_compatible", external_called=True, fallback_reason=None)
        except Exception as exc:
            if not self.settings.enable_model_fallback:
                raise
            return EmbeddingCallResult(
                vectors=[self._fake_embedding(text) for text in texts],
                provider="fake",
                external_called=True,
                fallback_reason=f"{type(exc).__name__}: {exc}",
            )

    async def _openai_compatible_embeddings(self, texts: list[str], text_type: str = "document") -> list[list[float]]:
        batch_size = self.settings.embedding_batch_size
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(await self._openai_compatible_embeddings_batch(texts[start : start + batch_size], text_type=text_type))
        return vectors

    async def _openai_compatible_embeddings_batch(self, texts: list[str], text_type: str = "document") -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self.settings.embedding_model,
            "input": texts,
            "encoding_format": "float",
            "dimensions": self.settings.embedding_dimensions,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        data = await post_openai_compatible_json(
            f"{self.settings.openai_base_url.rstrip('/')}/embeddings",
            payload,
            headers,
            timeout=60.0,
            resolve_ip=self.settings.openai_resolve_ip,
        )
        return [item["embedding"] for item in data["data"]]

    def _fake_embedding(self, text: str) -> list[float]:
        vector = []
        for idx in range(self.settings.embedding_dimensions):
            digest = hashlib.sha256(f"{idx}:{text}".encode("utf-8")).digest()
            value = int.from_bytes(digest[:4], "big") / 2**32
            vector.append((value * 2.0) - 1.0)
        magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / magnitude for value in vector]


@dataclass(frozen=True)
class EmbeddingCallResult:
    vectors: list[list[float]]
    provider: str
    external_called: bool
    fallback_reason: str | None = None


@dataclass(frozen=True)
class ChatCallResult:
    answer: str
    provider: str
    model: str
    external_called: bool
    fallback_reason: str | None = None


class ChatProvider:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def answer_question(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        return (await self.answer_question_with_meta(question, contexts, history)).answer

    async def answer_question_with_meta(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> ChatCallResult:
        if not contexts:
            return ChatCallResult(
                answer="I could not find enough reliable course context to answer this question with citations.",
                provider="none",
                model=self.settings.chat_model,
                external_called=False,
                fallback_reason="no_contexts",
            )
        if not self.settings.openai_api_key:
            if not self.settings.enable_model_fallback:
                raise FallbackDisabledError("OPENAI_API_KEY is required because ENABLE_MODEL_FALLBACK is false")
            return ChatCallResult(
                answer=self._extractive_answer(question, contexts),
                provider="extractive_fallback",
                model="local_extractive_template",
                external_called=False,
                fallback_reason="missing_openai_api_key",
            )
        try:
            answer = await self._openai_compatible_chat(question, contexts, history or [])
            return ChatCallResult(
                answer=answer,
                provider="openai_compatible_chat",
                model=self.settings.chat_model,
                external_called=True,
                fallback_reason=None,
            )
        except Exception as exc:
            if not self.settings.enable_model_fallback:
                raise
            return ChatCallResult(
                answer=self._extractive_answer(question, contexts),
                provider="extractive_fallback",
                model="local_extractive_template",
                external_called=True,
                fallback_reason=f"{type(exc).__name__}: {exc}",
            )

    async def extract_graph_payload(self, text: str, chapter: str | None, source_type: str) -> dict[str, Any]:
        if not self.settings.openai_api_key:
            if not self.settings.enable_model_fallback:
                raise FallbackDisabledError("OPENAI_API_KEY is required because ENABLE_MODEL_FALLBACK is false")
            return {"concepts": [], "relations": []}
        system_prompt = (
            "You extract a course knowledge graph from teaching material. "
            "Return JSON only with keys concepts and relations. "
            "Each concept must contain name, aliases, summary, concept_type, importance_score. "
            "Each relation must contain source, target, relation_type, confidence. "
            "Allowed relation_type values: defines, relates_to, prerequisite_of, example_of, solves, compares, extends, mentions. "
            "Extract 6 to 12 specific course concepts when the excerpt has enough substance, including algorithms, theorems, "
            "definitions, problem types, complexity classes, graph structures, and proof techniques. "
            "Extract 6 to 16 useful relations between those concepts. "
            "Every relation source and target must exactly match a concept name included in concepts. "
            "Prefer specific names like Breadth-First Search, Dijkstra Algorithm, Spanning Tree, Flow Network, NP-Complete, "
            "Matching, Cut, Planar Graph, Eulerian Tour, Hamiltonian Cycle, and Matrix Tree Theorem over generic words. "
            "Skip formatting artifacts, page headers, exercise labels, and generic words."
        )
        user_prompt = (
            f"chapter={chapter or 'General'}\n"
            f"source_type={source_type}\n"
            "material:\n"
            f"{text[:7000]}"
        )
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        payload: dict[str, Any] = {
            "model": self.settings.chat_model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        try:
            return await self._post_chat_json(payload)
        except Exception:
            if not self.settings.enable_model_fallback:
                raise
            return {"concepts": [], "relations": []}

    async def classify_json(self, system_prompt: str, user_prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.openai_api_key:
            if not self.settings.enable_model_fallback:
                raise FallbackDisabledError("OPENAI_API_KEY is required because ENABLE_MODEL_FALLBACK is false")
            return fallback
        payload: dict[str, Any] = {
            "model": self.settings.chat_model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        try:
            return await self._post_chat_json(payload)
        except Exception:
            if not self.settings.enable_model_fallback:
                raise
            return fallback

    async def rewrite_question(self, question: str, history: list[dict] | None = None) -> str:
        if not self.settings.openai_api_key:
            if not self.settings.enable_model_fallback:
                raise FallbackDisabledError("OPENAI_API_KEY is required because ENABLE_MODEL_FALLBACK is false")
            return question
        history_text = "\n".join(f"{item.get('role')}: {item.get('content')}" for item in (history or [])[-6:])
        payload: dict[str, Any] = {
            "model": self.settings.chat_model,
            "messages": [
                {
                    "role": "system",
                    "content": "Rewrite the user question as a concise standalone retrieval query. Return only the rewritten query.",
                },
                {"role": "user", "content": f"History:\n{history_text}\n\nQuestion:\n{question}"},
            ],
            "temperature": 0.0,
        }
        try:
            return (await self._post_chat_text(payload)).strip() or question
        except Exception:
            if not self.settings.enable_model_fallback:
                raise
            return question

    async def _openai_compatible_chat(self, question: str, contexts: list[dict], history: list[dict]) -> str:
        citations = "\n\n".join(
            f"[{idx + 1}] {item['document_title']} / {item.get('chapter') or 'General'}\n{item['content']}"
            for idx, item in enumerate(contexts)
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a course knowledge-base assistant. "
                    "Answer only from the supplied course excerpts and do not invent unsupported facts. "
                    "Keep the answer direct, concise, and say when the evidence is insufficient. "
                    "Format the answer as clean GitHub-flavored Markdown. "
                    "When writing mathematical notation, use valid LaTeX only: inline variables and short expressions "
                    "must be wrapped in single dollar delimiters like $k_i$ and $n - 1$; important equations must be "
                    "placed in display math blocks using double dollar delimiters on their own lines. "
                    "Never write formulas as glued plain text such as n-1ki, k_iin, or C(i)=n-1ki. "
                    "Use LaTeX commands and braces for fractions, superscripts, subscripts, and named variants, for example "
                    "$$C_D(i) = \\frac{k_i}{n - 1}$$, $k_i^{\\text{in}}$, and $k_i^{\\text{out}}$. "
                    "Do not repeat the same formula in both prose and math form; write the equation once, then explain "
                    "each symbol in separate bullets or sentences."
                ),
            },
            *history,
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    "Course excerpts:\n"
                    f"{citations}\n\n"
                    "Before finalizing, check that every formula is either inline LaTeX or display LaTeX, "
                    "and that variables are not attached to neighboring words."
                ),
            },
        ]
        payload = {"model": self.settings.chat_model, "messages": messages, "temperature": 0.2}
        return await self._post_chat_text(payload)

    async def _post_chat_text(self, payload: dict[str, Any]) -> str:
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        data = await post_openai_compatible_json(
            f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
            payload,
            headers,
            timeout=90.0,
            resolve_ip=self.settings.openai_resolve_ip,
        )
        return data["choices"][0]["message"]["content"]

    async def _post_chat_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._parse_json_object(await self._post_chat_text(payload))

    def _extractive_answer(self, question: str, contexts: list[dict]) -> str:
        lead = next((item for item in contexts if item.get("metadata", {}).get("content_kind") != "code"), contexts[0])
        lines = [
            f"The strongest course source is {lead['document_title']} in {lead.get('chapter') or 'the relevant section'}.",
            lead["snippet"],
        ]
        if len(contexts) > 1:
            lines.append("Other retrieved excerpts provide related background; use the citations to inspect the source material.")
        lines.append(f"Question: {question}")
        return "\n".join(lines)

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                return {}
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}


async def post_openai_compatible_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    timeout: float,
    resolve_ip: str | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            if resolve_ip:
                return await asyncio.to_thread(_post_json_with_curl_resolve, url, payload, headers, timeout, resolve_ip)
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            last_error = exc
            if attempt >= 3 or not _is_retryable_openai_error(exc):
                raise
            await asyncio.sleep(float(attempt))
    raise RuntimeError(f"OpenAI-compatible request failed: {last_error}")


def _is_retryable_openai_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    message = str(exc).lower()
    non_retryable_markers = ("invalidparameter", "invalid parameter", "unauthorized", "forbidden", "401", "403")
    if any(marker in message for marker in non_retryable_markers):
        return False
    retryable_markers = (
        "timeout",
        "timed out",
        "operation timed out",
        "failed to connect",
        "could not connect",
        "connection reset",
        "handshake",
        "schannel",
        "ssl/tls",
        "temporarily unavailable",
        "curl: (28)",
        "curl: (7)",
        "curl: (35)",
    )
    return any(marker in message for marker in retryable_markers)


def _post_json_with_curl_resolve(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    resolve_ip: str,
) -> dict[str, Any]:
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(f"Invalid OpenAI-compatible URL: {url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    payload_path = None
    config_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".json") as payload_file:
            json.dump(payload, payload_file, ensure_ascii=False)
            payload_path = payload_file.name
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".curl") as config_file:
            payload_ref = payload_path.replace("\\", "/")
            config_file.write(f'url = "{url}"\n')
            config_file.write("request = POST\n")
            config_file.write(f'connect-timeout = {min(int(timeout), 30)}\n')
            config_file.write(f'max-time = {int(timeout)}\n')
            config_file.write(f'resolve = "{parsed.hostname}:{port}:{resolve_ip}"\n')
            config_file.write(f'header = "Authorization: {headers["Authorization"]}"\n')
            config_file.write('header = "Content-Type: application/json"\n')
            config_file.write(f'data-binary = "@{payload_ref}"\n')
            config_path = config_file.name
        result = subprocess.run(
            ["curl.exe", "-sS", "-K", config_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout + 10,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"curl exited with {result.returncode}")
        data = json.loads(result.stdout)
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(json.dumps(data["error"], ensure_ascii=False))
        return data
    finally:
        for path in (payload_path, config_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
