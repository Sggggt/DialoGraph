from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_openai_compatible_embeddings_are_batched(no_fallback_env, monkeypatch):
    from app.core.config import get_settings
    from app.services import embeddings
    from app.services.embeddings import EmbeddingProvider

    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "10")
    get_settings.cache_clear()
    calls: list[list[str]] = []

    async def fake_post_json(url, payload, headers, *, timeout, resolve_ip=None):
        batch = list(payload["input"])
        calls.append(batch)
        offset = sum(len(call) for call in calls[:-1])
        return {"data": [{"embedding": [float(offset + index)]} for index, _text in enumerate(batch)]}

    monkeypatch.setattr(embeddings, "post_openai_compatible_json", fake_post_json)

    result = await EmbeddingProvider().embed_texts_with_meta([f"text-{index}" for index in range(25)])

    assert [len(call) for call in calls] == [10, 10, 5]
    assert result.provider == "openai_compatible"
    assert result.external_called is True
    assert result.fallback_reason is None
    assert result.vectors == [[float(index)] for index in range(25)]


@pytest.mark.asyncio
async def test_openai_compatible_request_retries_transient_errors(no_fallback_env, monkeypatch):
    from app.services import embeddings

    calls = 0

    def flaky_curl(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("curl: (35) schannel: failed to receive handshake, SSL/TLS connection failed")
        return {"ok": True}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(embeddings, "_post_json_with_curl_resolve", flaky_curl)
    monkeypatch.setattr(embeddings.asyncio, "sleep", no_sleep)

    result = await embeddings.post_openai_compatible_json(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        {"model": "unit-test"},
        {"Authorization": "Bearer unit-test", "Content-Type": "application/json"},
        timeout=1,
        resolve_ip="127.0.0.1",
    )

    assert result == {"ok": True}
    assert calls == 3


@pytest.mark.asyncio
async def test_openai_compatible_request_does_not_retry_invalid_parameters(no_fallback_env, monkeypatch):
    from app.services import embeddings

    calls = 0

    def invalid_parameter(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("<400> InternalError.Algo.InvalidParameter: batch size is invalid")

    monkeypatch.setattr(embeddings, "_post_json_with_curl_resolve", invalid_parameter)

    with pytest.raises(RuntimeError, match="InvalidParameter"):
        await embeddings.post_openai_compatible_json(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            {"model": "unit-test"},
            {"Authorization": "Bearer unit-test", "Content-Type": "application/json"},
            timeout=1,
            resolve_ip="127.0.0.1",
        )

    assert calls == 1
