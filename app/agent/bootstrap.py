"""Legacy environment bootstrap used only by the Agents CLI compatibility path."""

from .config import load_settings

SETTINGS = load_settings()

__all__ = ["SETTINGS"]
