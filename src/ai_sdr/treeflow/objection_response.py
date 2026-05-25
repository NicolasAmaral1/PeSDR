"""Build the message list for an inline objection response (Plan 4a, spec §4.4).

Inherits persona from the active Node, appends an objection-specific instruction
block, and ends with the KB-content block (or a defensive instruction when KB
is empty). Cache control follows the same pattern as build_system_messages."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage

from ai_sdr.schemas.treeflow_yaml import GlobalObjection, NodeObjection, NodeSpec

_OBJECTION_PREFIX = (
    "O lead levantou uma objeção identificada como '{id}' ({description}). "
    "Use o conhecimento abaixo. Não tente avançar a conversa nem coletar campos — "
    "apenas resolva a preocupação e convide a continuar."
)

_KB_EMPTY_FALLBACK = (
    "Não temos informações suficientes no momento sobre esta objeção. "
    "Peça mais detalhes ao lead em vez de inventar números ou afirmações."
)


def _objection_text(objection: NodeObjection | GlobalObjection) -> str:
    return _OBJECTION_PREFIX.format(id=objection.id, description=objection.description)


def _kb_block(kb_content: str) -> str:
    if kb_content.strip():
        return f"<knowledge_base>\n{kb_content}\n</knowledge_base>"
    return _KB_EMPTY_FALLBACK


def build_inline_objection_messages(
    *,
    node: NodeSpec,
    objection: NodeObjection | GlobalObjection,
    kb_content: str,
    conversation: list[BaseMessage],
    cache_enabled: bool,
    provider: str,
) -> list[BaseMessage]:
    """SystemMessage (persona + objection prefix + KB) + the conversation."""
    persona = node.prompt
    objection_prefix = _objection_text(objection)
    kb_text = _kb_block(kb_content)

    if provider == "anthropic":
        block1: dict[str, Any] = {"type": "text", "text": persona}
        block2: dict[str, Any] = {"type": "text", "text": objection_prefix}
        if cache_enabled:
            block1["cache_control"] = {"type": "ephemeral"}
            block2["cache_control"] = {"type": "ephemeral"}
        block3: dict[str, Any] = {"type": "text", "text": kb_text}
        system = SystemMessage(content=[block1, block2, block3])
    else:
        system = SystemMessage(content="\n\n".join([persona, objection_prefix, kb_text]))

    return [system, *conversation]
