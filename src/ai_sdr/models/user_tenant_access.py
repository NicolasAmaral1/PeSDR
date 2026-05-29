"""UserTenantAccess — many-to-many between users and tenants with a role.

Composite PK (user_id, tenant_id) means a user has AT MOST one role per
tenant. Role is one of 'operator' or 'tenant_admin' (CHECK constraint
in migration 0009). v1 treats them identically; tenant_admin gains
distinct privileges in a future plan.

is_platform_admin on the User itself bypasses this table — platform
admins have implicit access to every tenant. See web/auth.py for the
require_tenant_access dep that enforces this.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base

UserTenantRole = Literal["operator", "tenant_admin"]


class UserTenantAccess(Base):
    __tablename__ = "user_tenant_access"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(Text(), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
