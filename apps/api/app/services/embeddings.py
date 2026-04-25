from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings


def is_degraded_mode() -> bool:
    settings = get_settings()
    return (not settings.dashscope_api_key) or settings.enable_fake_embeddings or settings.enable_fake_chat


class EmbeddingProvider:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def embed_texts(self, texts: list[str], text_type: str = "document") -> list[list[float]]:
        return (await self.embed_texts_with_meta(texts, text_type=text_type)).vectors

    async def embed_texts_with_meta(self, texts: list[str], text_type: str = "document") -> "EmbeddingCallResult":
        if not texts:
            return EmbeddingCallResult(vectors=[], provider="fake", external_called=False, fallback_reason=None)
        if self.settings.enable_fake_embeddings:
            return EmbeddingCallResult(
                vectors=[self._fake_embedding(text) for text in texts],
                provider="fake",
                external_called=False,
                fallback_reason="fake_embeddings_enabled",
            )
        if not self.settings.dashscope_api_key:
            return EmbeddingCallResult(
                vectors=[self._fake_embedding(text) for text in texts],
                provider="fake",
                external_called=False,
                fallback_reason="missing_dashscope_api_key",
            )
        try:
            vectors = await self._dashscope_embeddings(texts, text_type=text_type)
            return EmbeddingCallResult(vectors=vectors, provider="dashscope", external_called=True, fallback_reason=None)
        except Exception as exc:
            return EmbeddingCallResult(
                vectors=[self._fake_embedding(text) for text in texts],
                provider="fake",
                external_called=True,
                fallback_reason=f"{type(exc).__name__}: {exc}",
            )

    async def _dashscope_embeddings(self, texts: list[str], text_type: str = "document") -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self.settings.embedding_model,
            "input": texts,
            "encoding_format": "float",
            "dimensions": self.settings.embedding_dimensions,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.dashscope_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.post(f"{self.settings.dashscope_base_url}/embeddings", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
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
        if not self.settings.dashscope_api_key or self.settings.enable_fake_chat:
            fallback_reason = "fake_chat_enabled" if self.settings.enable_fake_chat else "missing_dashscope_api_key"
            return ChatCallResult(
                answer=self._extractive_answer(question, contexts),
                provider="extractive_fallback",
                model="local_extractive_template",
                external_called=False,
                fallback_reason=fallback_reason,
            )
        try:
            answer = await self._dashscope_chat(question, contexts, history or [])
            return ChatCallResult(
                answer=answer,
                provider="dashscope_chat",
                model=self.settings.chat_model,
                external_called=True,
                fallback_reason=None,
            )
        except Exception as exc:
            return ChatCallResult(
                answer=self._extractive_answer(question, contexts),
                provider="extractive_fallback",
                model="local_extractive_template",
                external_called=True,
                fallback_reason=f"{type(exc).__name__}: {exc}",
            )

    async def extract_graph_payload(self, text: str, chapter: str | None, source_type: str) -> dict[str, Any]:
        if not self.settings.dashscope_api_key or self.settings.enable_fake_chat:
            return {"concepts": [], "relations": []}
        system_prompt = (
            "You extract a compact course knowledge graph from teaching material. "
            "Return JSON only with keys concepts and relations. "
            "Each concept must contain name, aliases, summary, concept_type, importance_score. "
            "Each relation must contain source, target, relation_type, confidence. "
            "Allowed relation_type values: defines, relates_to, prerequisite_of, example_of, solves, compares, extends, mentions. "
            "Keep only course-relevant concepts. Skip generic words and formatting artifacts."
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
            return {"concepts": [], "relations": []}

    async def classify_json(self, system_prompt: str, user_prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.dashscope_api_key or self.settings.enable_fake_chat:
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
            return fallback

    async def rewrite_question(self, question: str, history: list[dict] | None = None) -> str:
        if not self.settings.dashscope_api_key or self.settings.enable_fake_chat:
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
            return question

    async def _dashscope_chat(self, question: str, contexts: list[dict], history: list[dict]) -> str:
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
                    "Keep the answer direct, concise, and say when the evidence is insufficient."
                ),
            },
            *history,
            {"role": "user", "content": f"Question: {question}\n\nCourse excerpts:\n{citations}"},
        ]
        payload = {"model": self.settings.chat_model, "messages": messages, "temperature": 0.2}
        return await self._post_chat_text(payload)

    async def _post_chat_text(self, payload: dict[str, Any]) -> str:
        headers = {
            "Authorization": f"Bearer {self.settings.dashscope_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
            response = await client.post(f"{self.settings.dashscope_base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
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
