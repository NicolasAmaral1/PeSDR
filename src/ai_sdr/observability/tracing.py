"""LangSmith tracing metadata helper.

build_trace_metadata produces the dict that callers attach to a
langchain ainvoke via `config={"metadata": ...}`. The dict only
includes keys that were passed — empty fields don't appear, so the
LangSmith dashboard isn't cluttered with null values.

trace_origin is REQUIRED (typed Literal). Every other field is
optional. Sub-traces (e.g., classifier inside graph.ainvoke) inherit
metadata from parent — but each site still passes its own
trace_origin so direct filtering (`metadata.trace_origin = "X"`) in
the dashboard works without depending on the parent context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ai_sdr.models.lead import Lead
    from ai_sdr.models.talkflow import TalkFlow
    from ai_sdr.models.tenant import Tenant


TraceOrigin = Literal[
    "process_lead_inbox",
    "follow_up_scanner",
    "window_expired_recovery",
    "simulate",
    "objection_classifier",
    "guardrails_critic",
    "field_extractor",
]


def build_trace_metadata(
    *,
    tenant: Tenant | None = None,
    talkflow: TalkFlow | None = None,
    lead: Lead | None = None,
    node: str | None = None,
    turn_index: int | None = None,
    trace_origin: TraceOrigin,
) -> dict[str, Any]:
    """Build the langchain RunnableConfig.metadata dict.

    Returns a flat dict with only the populated keys. trace_origin is
    always present. Other fields appear only when their corresponding
    argument is not None.
    """
    metadata: dict[str, Any] = {"trace_origin": trace_origin}
    if tenant is not None:
        metadata["tenant_id"] = str(tenant.id)
        metadata["tenant_slug"] = tenant.slug
    if talkflow is not None:
        metadata["talkflow_id"] = str(talkflow.id)
    if lead is not None:
        metadata["lead_id"] = str(lead.id)
    if node is not None:
        metadata["node"] = node
    if turn_index is not None:
        metadata["turn_index"] = turn_index
    return metadata
