"""User — a global identity that can access one or more tenants' consoles.

Users are NOT tenant-scoped (the join table user_tenant_access maps a
user to N tenants with a role per tenant). The table has no RLS because
authorization happens in the app layer (see web/auth.py); RLS would
create a chicken-and-egg problem (must be authenticated to query auth).

is_platform_admin is a flag at user level for cross-tenant access. v1
uses it for the require_tenant_access dep (admin bypasses the
user_tenant_access check); P11b will add cross-tenant UI routes that
also gate on this flag.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    username: Mapped[str] = mapped_column(Text(), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, server_default=func.false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
