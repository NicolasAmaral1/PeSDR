"""Form provider adapters subsystem (4ª borda nova do PeSDR).

Side-effect import pra registrar os adapters no FORM_PROVIDERS registry.
Padrão idêntico ao usado em `messaging/__init__.py` e `flowengine/actions/__init__.py`.
"""
from ai_sdr.forms import respondi  # noqa: F401 — registra RespondiFormAdapter

# Quando novos providers entrarem (Typeform, Tally, Google Forms, etc.),
# adicionar import aqui pra side-effect.
# from ai_sdr.forms import typeform  # noqa: F401
