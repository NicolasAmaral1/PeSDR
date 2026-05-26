"""SOPS-based secrets loader (uses `sops --decrypt`)."""

from ai_sdr.secrets.sops_loader import SopsDecryptError, SopsLoader

__all__ = ["SopsLoader", "SopsDecryptError"]
