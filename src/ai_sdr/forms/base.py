"""FormProviderAdapter contract — the boundary between PeSDR runtime and a
form provider (Respondi, Typeform, etc).

Conceptual parallel to MessagingAdapter:

  - MessagingAdapter ingests **messages** (text from lead, ongoing conversation).
  - FormProviderAdapter ingests **submissions** (one-shot lead origination
    from a web form, no prior conversation).

Adapter purity invariants:
  - Knows nothing about the `leads` or `tenants` tables.
  - Receives tenant-specific config + secrets at construction; never reads
    them at request time.
  - `handle_submission` validates auth, parses, normalizes — no DB writes.
    Persistence and enqueueing happen in `forms.ingest`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LeadIdentifier:
    """How we resolve the lead from a form submission.

    At least one of `whatsapp_e164`, `email`, or `external_label` must be
    present — the ingest layer raises IdentityResolutionError otherwise.

    For the Manoela pilot, `whatsapp_e164` is the primary key (it's the
    channel the agent will message on). `email` and `external_label` are
    fallback metadata and don't drive lead matching at MVP — Plan 6
    (Identity Resolver) formalizes matching when needed.
    """

    whatsapp_e164: str | None = None
    email: str | None = None
    external_label: str | None = None

    def __post_init__(self) -> None:
        if not any((self.whatsapp_e164, self.email, self.external_label)):
            raise ValueError(
                "LeadIdentifier requires at least one of whatsapp_e164/email/external_label"
            )


@dataclass(frozen=True)
class IngestedFormSubmission:
    """A normalized form submission produced by `handle_submission`.

    `external_id` is the provider-native submission id used for idempotent
    dedupe at the persistence layer (UNIQUE on inbound_form_submissions
    (tenant_id, provider, external_id)).

    `field_values` is the result of applying the tenant's `field_mapping`
    to the provider's raw answers. Special key `whatsapp_e164` is moved
    into `LeadIdentifier` before being passed to `field_values` — the
    persisted JSONB never contains it (the canonical home is `leads.whatsapp_e164`).

    `source_meta` carries provider-specific metadata (form_id, utms,
    submission status, etc) — preserved for audit / future BI.
    """

    external_id: str
    submitted_at_iso: str
    lead_identifier: LeadIdentifier
    field_values: dict[str, Any] = field(default_factory=dict)
    source_meta: dict[str, Any] = field(default_factory=dict)
    raw: Mapping[str, object] = field(default_factory=dict)


class FormProviderAdapter(ABC):
    """Boundary between PeSDR runtime and a form provider."""

    @abstractmethod
    async def handle_submission(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        url_params: Mapping[str, str],
    ) -> IngestedFormSubmission:
        """Validate signature/shared secret, parse, normalize.

        Raises:
          - SignatureError  if auth fails (caller returns 401).
          - MalformedPayload if the body shape is unrecognized (caller returns 400).
        """
