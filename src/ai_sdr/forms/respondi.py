"""RespondiFormAdapter — parse Respondi webhook payloads into IngestedFormSubmission.

Payload shape (validated against a real form on 2026-06-25):

  {
    "form": {"form_name": "...", "form_id": "QWHmKbnx"},
    "respondent": {
      "status": "completed",
      "date": "2026-06-25 23:46:22",          # naive timestamp, BRT
      "score": null,
      "respondent_id": "<uuid>",              # dedup key
      "answers": {...},                       # title → answer (key UNSTABLE if title edits)
      "raw_answers": [                        # USE THIS — question_id is stable
        {
          "question": {
            "question_title": "...",
            "question_id": "xlcbkl7s88q",
            "question_type": "name" | "text" | "textarea" | "email" |
                             "phone" | "rating" | "radio"
          },
          "answer": <varies by type>
        },
        ...
      ]
    }
  }

Per-question_type normalization:
  - phone:    {"country": "55", "phone": "43996819949"} → "+5543996819949"
  - radio:    ["Option Text"]                            → "Option Text"
  - email:    "x@y.com"                                  → "x@y.com" (lowercase)
  - rating:   "5"                                        → "5"
  - other:    str(answer)                                → str(answer)

Authentication: Respondi does NOT support HMAC. Auth via shared secret as a
URL query param (`?secret=...`), validated via `hmac.compare_digest`.
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping
from typing import Any

from ai_sdr.forms.base import (
    FormProviderAdapter,
    IngestedFormSubmission,
    LeadIdentifier,
)
from ai_sdr.forms.errors import MalformedPayload, SignatureError
from ai_sdr.forms.factory import register_provider, resolve_secret
from ai_sdr.schemas.tenant_yaml import FormProviderConfig

_PHONE_DIGITS_ONLY = re.compile(r"\D")


class RespondiFormAdapter(FormProviderAdapter):
    def __init__(
        self,
        cfg: FormProviderConfig,
        secrets: Mapping[str, str],
    ) -> None:
        self._cfg = cfg
        self._shared_secret = resolve_secret(cfg.shared_secret_ref, secrets)
        # Inverted field_mapping: same source-of-truth, indexed by question_id.
        # tenant.yaml authors map `question_id → collected_field`; we use it
        # in the same direction here.
        self._field_mapping = cfg.field_mapping

    async def handle_submission(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        url_params: Mapping[str, str],
    ) -> IngestedFormSubmission:
        self._verify_signature(url_params)

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise MalformedPayload(f"invalid JSON body: {exc}") from exc

        if not isinstance(payload, dict):
            raise MalformedPayload("top-level payload must be a JSON object")

        form_meta = payload.get("form") or {}
        respondent = payload.get("respondent") or {}
        if not isinstance(respondent, dict):
            raise MalformedPayload("respondent block missing or not an object")

        external_id = respondent.get("respondent_id")
        if not isinstance(external_id, str) or not external_id:
            raise MalformedPayload("respondent.respondent_id required")

        submitted_at_iso = self._extract_submitted_at(respondent)

        field_values, whatsapp_e164, email = self._extract_field_values(
            respondent.get("raw_answers") or []
        )

        identifier = LeadIdentifier(
            whatsapp_e164=whatsapp_e164,
            email=email,
            external_label=external_id,
        )

        return IngestedFormSubmission(
            external_id=external_id,
            submitted_at_iso=submitted_at_iso,
            lead_identifier=identifier,
            field_values=field_values,
            source_meta={
                "form_id": form_meta.get("form_id"),
                "form_name": form_meta.get("form_name"),
                "status": respondent.get("status"),
                "score": respondent.get("score"),
            },
            raw=payload,
        )

    # -- helpers --------------------------------------------------------------

    def _verify_signature(self, url_params: Mapping[str, str]) -> None:
        """Constant-time compare of the URL `secret` query param against the
        adapter's configured shared_secret. Raises SignatureError on mismatch.
        """
        if self._shared_secret is None:
            # Tenant didn't configure a secret (schema validator would have
            # blocked this when enabled=true) — treat as misconfiguration.
            raise SignatureError("form provider has no shared_secret configured")
        provided = url_params.get("secret", "")
        if not hmac.compare_digest(provided, self._shared_secret):
            raise SignatureError("shared secret mismatch")

    @staticmethod
    def _extract_submitted_at(respondent: dict[str, Any]) -> str:
        """Respondi sends `"2026-06-25 23:46:22"` (naive, BRT). The worker
        stores it as a TIMESTAMPTZ; for now we treat it as naive and let the
        DB cast (Postgres assumes UTC on naive input). Not ideal for time
        math but acceptable at MVP — actual lead processing is "now"-based
        anyway. TODO: pass tenant timezone here when needed.
        """
        raw = respondent.get("date") or ""
        if not isinstance(raw, str) or not raw:
            raise MalformedPayload("respondent.date missing")
        # Replace space with T for ISO 8601 compatibility.
        if " " in raw and "T" not in raw:
            raw = raw.replace(" ", "T", 1)
        return raw

    def _extract_field_values(
        self, raw_answers: list[Any]
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """Walk raw_answers, apply field_mapping, return (collected, phone, email).

        `collected` is the dict to pre-populate TalkFlowState.collected.
        `phone` and `email` are split out separately because they belong to
        the Lead, not the conversation state.
        """
        field_values: dict[str, Any] = {}
        whatsapp_e164: str | None = None
        email: str | None = None

        for item in raw_answers:
            if not isinstance(item, dict):
                continue
            question = item.get("question") or {}
            qid = question.get("question_id")
            qtype = question.get("question_type")
            answer = item.get("answer")
            if not isinstance(qid, str):
                continue

            target = self._field_mapping.get(qid)
            if target is None:
                continue  # field not mapped — ignored

            normalized = self._normalize_answer(qtype, answer)

            if target == "whatsapp_e164":
                if isinstance(normalized, str) and normalized:
                    whatsapp_e164 = normalized
                continue
            if target == "email":
                if isinstance(normalized, str) and normalized:
                    email = normalized
                # Also expose in field_values so the LLM can address by email
                # if the conversation ever needs it.
                field_values[target] = normalized
                continue

            field_values[target] = normalized

        return field_values, whatsapp_e164, email

    @staticmethod
    def _normalize_answer(question_type: str | None, answer: Any) -> Any:
        """Per-type coercion. Returns the value as it should appear in
        `collected` (or as the LeadIdentifier field for phone/email).
        """
        if question_type == "phone" and isinstance(answer, dict):
            country = str(answer.get("country") or "").strip()
            phone = str(answer.get("phone") or "").strip()
            digits = _PHONE_DIGITS_ONLY.sub("", country + phone)
            if not digits:
                return None
            return f"+{digits}"

        if question_type == "email" and isinstance(answer, str):
            return answer.strip().lower()

        if question_type == "radio" and isinstance(answer, list):
            if not answer:
                return None
            # Single-select disguised as array — take first.
            return str(answer[0])

        if isinstance(answer, list):
            # Multi-select / generic array — keep as-is, stringify items.
            return [str(x) for x in answer]

        if answer is None:
            return None

        return str(answer)


@register_provider("respondi")
def _build(cfg: FormProviderConfig, secrets: Mapping[str, str]) -> FormProviderAdapter:
    return RespondiFormAdapter(cfg, secrets)
