"""`ai-sdr users` — operator account management.

All 6 commands open their own async engine + session (same pattern as
`ai-sdr simulate`). They write to the global users + user_tenant_access
tables (no RLS — these are auth-serving tables).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.settings import get_settings
from ai_sdr.web.passwords import hash_password

users_app = typer.Typer(help="Operator account management")
console = Console()


def _make_session() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    engine = create_async_engine(get_settings().database_url, future=True)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def _load_user(session: AsyncSession, username: str) -> User:
    user = (
        await session.execute(select(User).where(func.lower(User.username) == username.lower()))
    ).scalar_one_or_none()
    if user is None:
        console.print(f"[red]user not found: {username}[/red]")
        raise typer.Exit(1)
    return user


async def _load_tenant(session: AsyncSession, slug: str) -> Tenant:
    t = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if t is None:
        console.print(f"[red]tenant not found: {slug}[/red]")
        raise typer.Exit(1)
    return t


@users_app.command("add")
def add(
    username: Annotated[str, typer.Option("--username", prompt=True)],
    password: Annotated[
        str | None,
        typer.Option(
            "--password", help="Use only for scripting; otherwise omit for interactive prompt"
        ),
    ] = None,
    admin: Annotated[bool, typer.Option("--admin", help="Grant is_platform_admin")] = False,
) -> None:
    if password is None:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    asyncio.run(_add_async(username, password, admin))


async def _add_async(username: str, password: str, admin: bool) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        existing = (
            await session.execute(select(User).where(func.lower(User.username) == username.lower()))
        ).scalar_one_or_none()
        if existing is not None:
            console.print(f"[red]username already exists (case-insensitive): {username}[/red]")
            raise typer.Exit(1)
        user = User(
            username=username,
            password_hash=hash_password(password),
            is_platform_admin=admin,
        )
        session.add(user)
        await session.commit()
        console.print(f"[green]created user {username} (id={user.id})[/green]")
        if admin:
            console.print(
                "[yellow]is_platform_admin=true — has implicit access to all tenants[/yellow]"
            )
    await engine.dispose()


@users_app.command("grant")
def grant(
    username: Annotated[str, typer.Option("--username")],
    tenant: Annotated[str, typer.Option("--tenant")],
    role: Annotated[str, typer.Option("--role")] = "operator",
) -> None:
    if role not in ("operator", "tenant_admin"):
        console.print(f"[red]role must be 'operator' or 'tenant_admin' (got {role})[/red]")
        raise typer.Exit(1)
    asyncio.run(_grant_async(username, tenant, role))


async def _grant_async(username: str, tenant_slug: str, role: str) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        user = await _load_user(session, username)
        tenant = await _load_tenant(session, tenant_slug)
        existing = (
            await session.execute(
                select(UserTenantAccess).where(
                    UserTenantAccess.user_id == user.id,
                    UserTenantAccess.tenant_id == tenant.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.role = role
            console.print(f"[yellow]updated existing grant to role={role}[/yellow]")
        else:
            session.add(UserTenantAccess(user_id=user.id, tenant_id=tenant.id, role=role))
            console.print(f"[green]granted {role} on {tenant_slug} to {username}[/green]")
        await session.commit()
    await engine.dispose()


@users_app.command("revoke")
def revoke(
    username: Annotated[str, typer.Option("--username")],
    tenant: Annotated[str, typer.Option("--tenant")],
) -> None:
    asyncio.run(_revoke_async(username, tenant))


async def _revoke_async(username: str, tenant_slug: str) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        user = await _load_user(session, username)
        tenant = await _load_tenant(session, tenant_slug)
        existing = (
            await session.execute(
                select(UserTenantAccess).where(
                    UserTenantAccess.user_id == user.id,
                    UserTenantAccess.tenant_id == tenant.id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            console.print(
                f"[yellow]no grant exists for {username} on {tenant_slug} — no-op[/yellow]"
            )
        else:
            await session.delete(existing)
            await session.commit()
            console.print(f"[green]revoked {username} from {tenant_slug}[/green]")
    await engine.dispose()


@users_app.command("passwd")
def passwd(
    username: Annotated[str, typer.Option("--username")],
) -> None:
    new_password = typer.prompt("New password", hide_input=True, confirmation_prompt=True)
    asyncio.run(_passwd_async(username, new_password))


async def _passwd_async(username: str, new_password: str) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        user = await _load_user(session, username)
        user.password_hash = hash_password(new_password)
        await session.commit()
        console.print(f"[green]password updated for {username}[/green]")
    await engine.dispose()


@users_app.command("list")
def list_(
    tenant: Annotated[str | None, typer.Option("--tenant")] = None,
) -> None:
    asyncio.run(_list_async(tenant))


async def _list_async(tenant_slug: str | None) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        if tenant_slug:
            t = await _load_tenant(session, tenant_slug)
            rows = (
                await session.execute(
                    select(User, UserTenantAccess.role)
                    .join(UserTenantAccess, UserTenantAccess.user_id == User.id)
                    .where(UserTenantAccess.tenant_id == t.id)
                    .order_by(User.username)
                )
            ).all()
            table = Table(title=f"Users with access to tenant: {tenant_slug}")
            table.add_column("Username", no_wrap=True)
            table.add_column("Role")
            table.add_column("Admin")
            for user, role in rows:
                table.add_row(
                    user.username,
                    role,
                    "✓" if user.is_platform_admin else "",
                )
        else:
            all_users = (
                (await session.execute(select(User).order_by(User.username))).scalars().all()
            )
            table = Table(title="All users")
            table.add_column("Username", no_wrap=True)
            table.add_column("Admin")
            table.add_column("Created")
            table.add_column("Last login")
            for user in all_users:
                table.add_row(
                    user.username,
                    "✓" if user.is_platform_admin else "",
                    user.created_at.strftime("%Y-%m-%d"),
                    user.last_login_at.strftime("%Y-%m-%d %H:%M") if user.last_login_at else "—",
                )
        console.print(table)
    await engine.dispose()


@users_app.command("set-admin")
def set_admin(
    username: Annotated[str, typer.Option("--username")],
    admin: Annotated[bool, typer.Option("--admin")] = True,
) -> None:
    asyncio.run(_set_admin_async(username, admin))


async def _set_admin_async(username: str, admin: bool) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        user = await _load_user(session, username)
        user.is_platform_admin = admin
        await session.commit()
        flag = "[green]promoted[/green]" if admin else "[yellow]demoted[/yellow]"
        console.print(f"{flag} {username} — is_platform_admin={admin}")
    await engine.dispose()
