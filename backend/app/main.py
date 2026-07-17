"""Compatibility entrypoint for the focused Phase 1 control plane."""

from app.phase1_main import app, create_app

__all__ = ["app", "create_app"]
