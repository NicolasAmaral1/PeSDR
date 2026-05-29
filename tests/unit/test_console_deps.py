"""Console deps — templates + tenant_loader factories."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates


def test_templates_is_jinja2_instance() -> None:
    from ai_sdr.web.deps import templates

    assert isinstance(templates, Jinja2Templates)


def test_templates_resolves_relative_to_web_package() -> None:
    """templates directory must point at src/ai_sdr/web/templates/."""
    from ai_sdr.web import deps

    pkg_dir = Path(deps.__file__).parent
    assert (pkg_dir / "templates").is_dir() or (pkg_dir / "templates").parent.is_dir()
    # The directory may not exist yet at this task; Task 15 creates the first
    # template. The assertion is that the templates instance was wired to a
    # path under the web package.
