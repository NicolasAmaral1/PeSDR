"""RespondiFormAdapter — primeiro impl de FormProviderAdapter.

Respondi (https://respondi.app) é um construtor de formulários BR. Webhook
envia POST JSON quando form é submetido. Sem HMAC nativo — segurança via
shared secret na URL.

Payload shape:
    {
      "form": {"form_name": str, "form_id": str},
      "respondent": {
        "respondent_id": str (UUID),
        "date": str (ISO),
        "score": int | float | null,
        "status": str ("completed", ...),
        "respondent_utms": dict,
        "answers": dict[str, Any],  # key = pergunta como texto (mutável!)
        "raw_answers": [
          {
            "question": {
              "question_id": str,
              "question_type": str,
              "question_title": str,
            },
            "answer": Any,
          },
          ...
        ]
      }
    }

field_mapping no tenant.yaml usa question_id (estável), NÃO question_title (mutável).
"""
from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from typing import Any

from ai_sdr.forms.base import (
    FormProviderAdapter,
    IngestedFormSubmission,
    LeadIdentifier,
)
from ai_sdr.forms.errors import MalformedPayload, SignatureError
from ai_sdr.forms.registry import register


@register
class RespondiFormAdapter(FormProviderAdapter):
    """Adapter pra Respondi.app forms.

    Validação de segurança: shared_secret query param.
        URL configurada no painel Respondi:
        https://sdr.luminai.ia.br/webhooks/<slug>/form/respondi?secret=<SECRET>

        Adapter valida `query_params["secret"] == self.secrets["respondi_webhook_secret"]`
        via hmac.compare_digest (timing-safe).
    """

    name = "respondi"

    async def handle_submission(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        query_params: Mapping[str, str],
    ) -> IngestedFormSubmission:
        """Valida secret + parseia raw JSON + mapeia campos via field_mapping.

        Roteiro:
            1. Validate query_params["secret"] contra self.secrets["respondi_webhook_secret"].
            2. json.loads(raw_body) → payload dict.
            3. Extract respondent_id, date, raw_answers.
            4. Iterar raw_answers: por question_id, mapear pra field_value via
               tenant.yaml > forms.respondi.field_mapping.
            5. Se question_id mapeado pra "whatsapp_e164": normalizar pra E.164
               via phonenumbers (BR region default).
            6. Else: coerce por question_type (number, email, etc).
            7. Construir IngestedFormSubmission com lead_identifier + field_values + source_meta.

        Raises:
            SignatureError: secret mismatch.
            MalformedPayload: JSON inválido, campo obrigatório ausente, phone inválido.
        """
        # TODO: implementação real
        # _validate_secret(query_params, self.secrets)
        # payload = self._parse_json(raw_body)
        # ... mapping + normalization ...
        raise NotImplementedError("Fase A T5 — RespondiFormAdapter.handle_submission")

    # ─── helpers privados ─────────────────────────────────────────────────

    def _validate_secret(self, query_params: Mapping[str, str]) -> None:
        """Compara secret em query string contra secret cifrado."""
        # expected = self.secrets.get("respondi_webhook_secret", "")
        # received = query_params.get("secret", "")
        # if not expected or not hmac.compare_digest(expected, received):
        #     raise SignatureError("respondi: invalid or missing secret in query string")
        raise NotImplementedError

    def _coerce_answer(self, raw_answer: dict[str, Any]) -> Any:
        """Cast answer baseado em question_type."""
        # qtype = raw_answer["question"]["question_type"]
        # ans = raw_answer["answer"]
        # if qtype == "number" and isinstance(ans, str): return int(ans) if ans.isdigit() else float(ans)
        # if qtype == "email" and isinstance(ans, str): return ans.strip().lower()
        # return ans
        raise NotImplementedError

    def _normalize_phone_br(self, raw: Any) -> str:
        """Normaliza pra E.164 assumindo região BR.

        Lib: phonenumbers (https://github.com/daviddrysdale/python-phonenumbers).
        """
        # try:
        #     parsed = phonenumbers.parse(str(raw), "BR")
        #     if not phonenumbers.is_valid_number(parsed):
        #         raise MalformedPayload(f"respondi: invalid phone {raw!r}")
        #     return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        # except phonenumbers.NumberParseException as exc:
        #     raise MalformedPayload(f"respondi: invalid phone {raw!r}: {exc}")
        raise NotImplementedError
