"""Login + logout handlers (GET form / POST submit / GET logout).

The POST handler and logout are added in Task 11; this task creates the
file with just the GET form route so subsequent tasks can register it
on the router incrementally.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ai_sdr.web.deps import templates

router = APIRouter()


@router.get("/console/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {})
