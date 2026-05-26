"""Tests for build_embedder — factory wiring (no live API calls)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ai_sdr.kb.embeddings import Embedder, build_embedder
from ai_sdr.schemas.llm_yaml import EmbeddingsConfig


def test_build_embedder_with_default_config() -> None:
    secrets = {"openai_key": "sk-test-fake"}
    with patch("ai_sdr.kb.embeddings.OpenAIEmbeddings") as fake_cls:
        emb = build_embedder(secrets, EmbeddingsConfig())
    fake_cls.assert_called_once()
    kwargs = fake_cls.call_args.kwargs
    assert kwargs["model"] == "text-embedding-3-small"
    assert kwargs["openai_api_key"] == "sk-test-fake"
    assert isinstance(emb, Embedder)


def test_build_embedder_custom_model_and_key_ref() -> None:
    secrets = {"openai_key_alt": "sk-test-other"}
    cfg = EmbeddingsConfig(
        model="text-embedding-3-large",
        api_key_ref="secrets/openai_key_alt",
    )
    with patch("ai_sdr.kb.embeddings.OpenAIEmbeddings") as fake_cls:
        build_embedder(secrets, cfg)
    kwargs = fake_cls.call_args.kwargs
    assert kwargs["model"] == "text-embedding-3-large"
    assert kwargs["openai_api_key"] == "sk-test-other"


def test_build_embedder_missing_secret_raises_keyerror() -> None:
    with pytest.raises(KeyError, match="openai_key"):
        build_embedder({}, EmbeddingsConfig())


async def test_embedder_delegates_to_lc_async_methods() -> None:
    class _FakeLC:
        async def aembed_query(self, text: str) -> list[float]:
            return [0.1] * 1536

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.2] * 1536 for _ in texts]

    emb = Embedder(_FakeLC())  # type: ignore[arg-type]
    q = await emb.embed_query("hello")
    assert len(q) == 1536 and q[0] == 0.1

    docs = await emb.embed_documents(["a", "b"])
    assert len(docs) == 2 and len(docs[0]) == 1536 and docs[0][0] == 0.2
