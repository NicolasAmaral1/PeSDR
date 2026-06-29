"""Exception hierarchy for the form provider adapter boundary.

Same terminal/transient distinction as messaging/errors.py:

  - SignatureError       webhook auth failed → caller returns 401.
  - MalformedPayload     adapter couldn't parse the request body → 400.
  - UnknownProvider      tenant has no enabled config for this provider → 404.
  - FormError            base for everything else (logged, returns 500).
"""

from __future__ import annotations


class FormError(Exception):
    """Base for any form-related error."""


class SignatureError(FormError):
    """Webhook signature / shared-secret check failed. Caller returns 401."""


class MalformedPayload(FormError):
    """The provider sent a body shape we can't parse. Caller returns 400."""


class UnknownProvider(FormError):
    """Tenant has no `forms.<provider>` block (or it's disabled). 404."""


class IdentityResolutionError(FormError):
    """The submission has no usable lead identifier (no phone, no email).

    Worker marks the submission as `error` but the webhook still returns 200.
    """
