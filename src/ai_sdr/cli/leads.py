"""Lead operator CLI — list pending + assign treeflow.

Both commands hit the REST endpoints from Task 22 so the CLI and any
future UI go through one authorization/validation path.
"""

from __future__ import annotations

from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

leads_app = typer.Typer(help="Lead operator tools")
console = Console()


@leads_app.command("list-pending")
def list_pending(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
    api_base_url: Annotated[str, typer.Option("--api-base-url")] = "http://localhost:8200",
) -> None:
    """List leads with status='pending_assignment'."""
    url = f"{api_base_url}/tenants/{tenant}/leads/pending"
    r = httpx.get(url, timeout=10.0)
    r.raise_for_status()
    items = r.json()

    if not items:
        console.print("[yellow]no pending leads[/yellow]")
        return

    table = Table(title=f"Pending leads — {tenant}")
    table.add_column("Lead ID", no_wrap=True)
    table.add_column("WhatsApp")
    table.add_column("Label")
    table.add_column("Created")
    table.add_column("Queued", justify="right")
    for it in items:
        table.add_row(
            it["id"],
            it.get("whatsapp_e164") or "-",
            it.get("external_label") or "-",
            it["created_at"],
            str(it["queued_messages"]),
        )
    console.print(table)


@leads_app.command("assign-lead")
def assign_lead(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
    lead: Annotated[str, typer.Option("--lead", help="Lead UUID")],
    treeflow: Annotated[str, typer.Option("--treeflow", help="TreeFlow id to attach")],
    api_base_url: Annotated[str, typer.Option("--api-base-url")] = "http://localhost:8200",
) -> None:
    """Attach a treeflow to a pending lead; worker drains queued inbounds."""
    url = f"{api_base_url}/tenants/{tenant}/leads/{lead}/assign"
    r = httpx.post(url, json={"treeflow_id": treeflow}, timeout=10.0)
    if r.status_code == 404:
        console.print(f"[red]lead not found: {lead}[/red]")
        raise typer.Exit(1)
    if r.status_code == 409:
        console.print(f"[red]conflict: {r.json().get('detail', 'lead not pending')}[/red]")
        raise typer.Exit(1)
    r.raise_for_status()
    body = r.json()
    console.print(
        f"[green]Lead {lead} → treeflow {treeflow}. "
        f"Replaying {body['queued_messages_to_replay']} queued message(s).[/green]"
    )
    console.print(f"talkflow_id: {body['talkflow_id']}")
