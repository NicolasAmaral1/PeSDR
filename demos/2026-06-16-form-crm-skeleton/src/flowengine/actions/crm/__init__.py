"""CRM action subsystem.

Side-effect import:
- adapter.py registra CRMActionAdapter no FE-03c ACTION_ADAPTERS registry
- rdstation/__init__.py registra RDStationCRMBackend no CRM_BACKENDS registry

Quando novos backends entrarem (HubSpot, Pipedrive, Kommo, etc), adicionar
import aqui.
"""
from ai_sdr.flowengine.actions.crm import adapter  # noqa: F401
from ai_sdr.flowengine.actions.crm import rdstation  # noqa: F401

# from ai_sdr.flowengine.actions.crm import hubspot  # noqa: F401  # future
