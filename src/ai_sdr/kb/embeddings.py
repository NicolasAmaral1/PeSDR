"""Embedder factory — wraps langchain_openai.OpenAIEmbeddings for async use.

Single embedding model in MVP: text-embedding-3-small (1536d). Switch via
tenant.llm.embeddings in tenant.yaml.

API key lookup mirrors llm/factory.py: secrets dict is keyed by bare names
(SopsLoader output), so we strip the 'secrets/' prefix that the schema
validator enforces on EmbeddingsConfig.api_key_ref.
"""

from __future__ import annotations

from langchain_openai import OpenAIEmbeddings

from ai_sdr.schemas.llm_yaml import EmbeddingsConfig


class Embedder:
    """Async-only embedder. Hides the LangChain wrapper from callers."""

    def __init__(self, lc_embeddings: OpenAIEmbeddings) -> None:
        self._lc = lc_embeddings

    async def embed_query(self, text: str) -> list[float]:
        return await self._lc.aembed_query(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._lc.aembed_documents(texts)


def build_embedder(secrets: dict[str, str], cfg: EmbeddingsConfig) -> Embedder:
    """Read API key from secrets[<bare key>] and construct an Embedder.

    Strips the 'secrets/' prefix per the tenant.yaml convention; SopsLoader
    returns secrets keyed by bare names.
    """
    api_key = secrets[cfg.api_key_ref.removeprefix("secrets/")]
    # `openai_api_key` is the canonical field name; `api_key` is its alias.
    # Using the canonical name keeps mypy happy without an ignore.
    lc = OpenAIEmbeddings(model=cfg.model, openai_api_key=api_key)
    return Embedder(lc)
